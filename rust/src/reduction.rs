//! Detached reduction symbols and lazy Python action views.

use crate::parser::PyParsedSymbol;
use pyo3::class::gc::{PyTraverseError, PyVisit};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyInt, PyList, PySlice};
use std::sync::Mutex;

enum ReductionSymbol {
    Native(PyParsedSymbol),
    Python(Py<PyParsedSymbol>),
}

#[pyclass(frozen, name = "_ReductionData")]
pub struct ReductionData {
    symbols: Mutex<Vec<ReductionSymbol>>,
}

impl ReductionData {
    pub fn new(lhs: PyParsedSymbol, rhs: Vec<PyParsedSymbol>) -> Self {
        let mut symbols = Vec::with_capacity(rhs.len() + 1);
        symbols.push(ReductionSymbol::Native(lhs));
        symbols.extend(rhs.into_iter().map(ReductionSymbol::Native));
        Self {
            symbols: Mutex::new(symbols),
        }
    }

    fn len(&self) -> usize {
        self.symbols.lock().unwrap().len()
    }

    fn value(&self, py: Python<'_>, index: usize) -> PyResult<Py<PyAny>> {
        let symbols = self.symbols.lock().unwrap();
        let symbol = symbols.get(index).ok_or_else(|| {
            pyo3::exceptions::PyIndexError::new_err("reduction index out of range")
        })?;
        match symbol {
            ReductionSymbol::Native(symbol) => Ok(symbol.value.clone_ref(py)),
            ReductionSymbol::Python(symbol) => Ok(symbol.try_borrow(py)?.value.clone_ref(py)),
        }
    }

    fn set_value(&self, py: Python<'_>, index: usize, value: Py<PyAny>) -> PyResult<()> {
        let previous = {
            let mut symbols = self.symbols.lock().unwrap();
            let symbol = symbols.get_mut(index).ok_or_else(|| {
                pyo3::exceptions::PyIndexError::new_err("reduction index out of range")
            })?;
            match symbol {
                ReductionSymbol::Native(symbol) => std::mem::replace(&mut symbol.value, value),
                ReductionSymbol::Python(symbol) => {
                    std::mem::replace(&mut symbol.try_borrow_mut(py)?.value, value)
                }
            }
        };
        // A finalizer may read or edit the saved view after this assignment.
        drop(previous);
        Ok(())
    }

    fn materialize_symbol(&self, py: Python<'_>, index: usize) -> PyResult<Py<PyParsedSymbol>> {
        let snapshot = {
            let symbols = self.symbols.lock().unwrap();
            match symbols.get(index) {
                Some(ReductionSymbol::Python(symbol)) => return Ok(symbol.clone_ref(py)),
                Some(ReductionSymbol::Native(symbol)) => symbol.clone_symbol(py),
                None => {
                    return Err(pyo3::exceptions::PyIndexError::new_err(
                        "reduction index out of range",
                    ))
                }
            }
        };
        // Allocation can collect cycles or re-enter a saved view. Keep the
        // native record visible until the new object is ready to publish.
        let candidate = Py::new(py, snapshot)?;
        let previous = {
            let mut symbols = self.symbols.lock().unwrap();
            let entry = symbols.get_mut(index).ok_or_else(|| {
                pyo3::exceptions::PyIndexError::new_err("reduction index out of range")
            })?;
            if let ReductionSymbol::Python(symbol) = entry {
                return Ok(symbol.clone_ref(py));
            }
            let ReductionSymbol::Native(current) =
                std::mem::replace(entry, ReductionSymbol::Python(candidate.clone_ref(py)))
            else {
                unreachable!()
            };
            // A re-entrant action may have edited the native record meanwhile.
            std::mem::replace(&mut *candidate.try_borrow_mut(py)?, current)
        };
        drop(previous);
        Ok(candidate)
    }

    pub fn lhs_snapshot(&self, py: Python<'_>) -> PyResult<PyParsedSymbol> {
        let symbols = self.symbols.lock().unwrap();
        match symbols.first() {
            Some(ReductionSymbol::Native(symbol)) => Ok(symbol.clone_symbol(py)),
            Some(ReductionSymbol::Python(symbol)) => Ok(symbol.try_borrow(py)?.clone_symbol(py)),
            None => Err(pyo3::exceptions::PyRuntimeError::new_err(
                "reduction data was cleared",
            )),
        }
    }

    pub fn release_rhs_guard(&self) {
        let mut previous = {
            let mut symbols = self.symbols.lock().unwrap();
            let first_rhs = symbols.len().min(1);
            symbols.split_off(first_rhs)
        };
        while let Some(symbol) = previous.pop() {
            drop(symbol);
        }
    }
}

impl Drop for ReductionData {
    fn drop(&mut self) {
        // Match the original RHS list's reverse release order.
        while let Some(symbol) = self.symbols.get_mut().unwrap().pop() {
            drop(symbol);
        }
    }
}

#[pymethods]
impl ReductionData {
    pub fn result(&self, py: Python<'_>, result: Py<PyAny>) -> PyResult<Py<PyAny>> {
        if !result.is_none(py) {
            self.set_value(py, 0, result)?;
        }
        self.value(py, 0)
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Ok(symbols) = self.symbols.try_lock() {
            for symbol in symbols.iter() {
                match symbol {
                    ReductionSymbol::Native(symbol) => visit.call(&symbol.value)?,
                    ReductionSymbol::Python(symbol) => visit.call(symbol)?,
                }
            }
        }
        Ok(())
    }

    fn __clear__(&self) {
        let mut previous = std::mem::take(&mut *self.symbols.lock().unwrap());
        while let Some(symbol) = previous.pop() {
            drop(symbol);
        }
    }
}

#[pyclass(subclass, frozen, name = "_ProductionView")]
pub struct ProductionView {
    source: Mutex<Option<Py<ReductionData>>>,
    slice: Mutex<Option<Py<PyAny>>>,
    stack: Mutex<Option<Py<PyAny>>>,
}

impl ProductionView {
    pub fn holds_source(&self, data: &Py<ReductionData>) -> bool {
        self.source.lock().unwrap().as_ref().map(Py::as_ptr) == Some(data.as_ptr())
    }

    fn set_source(&self, value: &Bound<'_, PyAny>) {
        let source = value
            .cast::<ReductionData>()
            .ok()
            .map(|data| data.clone().unbind());
        let slice = source.is_none().then(|| value.clone().unbind());
        let previous = (
            std::mem::replace(&mut *self.source.lock().unwrap(), source),
            std::mem::replace(&mut *self.slice.lock().unwrap(), slice),
        );
        drop(previous);
    }

    fn materialized_slice(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if let Some(slice) = self.slice.lock().unwrap().as_ref() {
            return Ok(slice.clone_ref(py));
        }
        let source = self
            .source
            .lock()
            .unwrap()
            .as_ref()
            .map(|data| data.clone_ref(py));
        let items = if let Some(data) = &source {
            let data = data.get();
            (0..data.len())
                .map(|index| data.materialize_symbol(py, index))
                .collect::<PyResult<Vec<_>>>()?
        } else {
            Vec::new()
        };
        let candidate = PyList::new(py, items)?.into_any().unbind();
        // Respect edits made by GC/finalizers while allocating the slice.
        if let Some(slice) = self.slice.lock().unwrap().as_ref() {
            return Ok(slice.clone_ref(py));
        }
        let current_source = self.source.lock().unwrap().as_ref().map(Py::as_ptr);
        if current_source != source.as_ref().map(Py::as_ptr) {
            return self.materialized_slice(py);
        }
        *self.slice.lock().unwrap() = Some(candidate.clone_ref(py));
        let previous = self.source.lock().unwrap().take();
        drop(previous);
        Ok(candidate)
    }
}

#[pymethods]
impl ProductionView {
    #[new]
    #[pyo3(signature = (slice_items, stack=None, lexer=None, parser=None, namemap=None))]
    fn new(
        slice_items: &Bound<'_, PyAny>,
        stack: Option<Py<PyAny>>,
        lexer: Option<Py<PyAny>>,
        parser: Option<Py<PyAny>>,
        namemap: Option<Py<PyAny>>,
    ) -> Self {
        // The Python subclass owns these public attributes.
        let _ = (lexer, parser, namemap);
        let source = slice_items
            .cast::<ReductionData>()
            .ok()
            .map(|data| data.clone().unbind());
        let slice = source.is_none().then(|| slice_items.clone().unbind());
        Self {
            source: Mutex::new(source),
            slice: Mutex::new(slice),
            stack: Mutex::new(stack),
        }
    }

    #[getter(_slice)]
    fn get_slice(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.materialized_slice(py)
    }

    #[setter(_slice)]
    fn set_slice(&self, value: &Bound<'_, PyAny>) {
        self.set_source(value);
    }

    #[getter(_stack)]
    fn get_stack(&self, py: Python<'_>) -> Py<PyAny> {
        self.stack
            .lock()
            .unwrap()
            .as_ref()
            .map(|stack| stack.clone_ref(py))
            .unwrap_or_else(|| py.None())
    }

    #[setter(_stack)]
    fn set_stack(&self, value: Option<Py<PyAny>>) {
        let previous = std::mem::replace(&mut *self.stack.lock().unwrap(), value);
        drop(previous);
    }

    fn __len__(&self, py: Python<'_>) -> PyResult<usize> {
        if let Some(data) = self.source.lock().unwrap().as_ref() {
            return Ok(data.get().len());
        }
        self.materialized_slice(py)?.bind(py).len()
    }

    fn __getitem__(&self, py: Python<'_>, index: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        if index.is_instance_of::<PySlice>() {
            let selected = self.materialized_slice(py)?.bind(py).get_item(index)?;
            let values = selected
                .try_iter()?
                .map(|symbol| symbol?.getattr("value"))
                .collect::<PyResult<Vec<_>>>()?;
            return Ok(PyList::new(py, values)?.into_any().unbind());
        }
        if !index.get_type().is(py.get_type::<PyInt>()) {
            // Preserve Python's comparison/index protocol for custom indices.
            if index.ge(0)? {
                return Ok(self
                    .materialized_slice(py)?
                    .bind(py)
                    .get_item(index)?
                    .getattr(intern!(py, "value"))?
                    .unbind());
            }
            let stack = self
                .stack
                .lock()
                .unwrap()
                .as_ref()
                .map(|stack| stack.clone_ref(py));
            return match stack {
                Some(stack) => Ok(stack
                    .bind(py)
                    .get_item(index)?
                    .getattr(intern!(py, "value"))?
                    .unbind()),
                None => Err(pyo3::exceptions::PyIndexError::new_err(
                    "Stack is not available for negative index",
                )),
            };
        }
        let number = match index.extract::<isize>() {
            Ok(number) => number,
            Err(error) if error.is_instance_of::<pyo3::exceptions::PyOverflowError>(py) => {
                if index.lt(0)? {
                    let stack = self
                        .stack
                        .lock()
                        .unwrap()
                        .as_ref()
                        .map(|stack| stack.clone_ref(py));
                    if let Some(stack) = stack {
                        return Ok(stack
                            .bind(py)
                            .get_item(index)?
                            .getattr(intern!(py, "value"))?
                            .unbind());
                    }
                    return Err(pyo3::exceptions::PyIndexError::new_err(
                        "Stack is not available for negative index",
                    ));
                }
                return Err(pyo3::exceptions::PyIndexError::new_err(
                    "reduction index out of range",
                ));
            }
            Err(error) => return Err(error),
        };
        if number < 0 {
            let stack = self
                .stack
                .lock()
                .unwrap()
                .as_ref()
                .map(|stack| stack.clone_ref(py));
            return match stack {
                Some(stack) => Ok(stack.bind(py).get_item(index)?.getattr("value")?.unbind()),
                None => Err(pyo3::exceptions::PyIndexError::new_err(
                    "Stack is not available for negative index",
                )),
            };
        }
        let source = self.source.lock().unwrap();
        if let Some(data) = source.as_ref() {
            return data.get().value(py, number as usize);
        }
        drop(source);
        Ok(self
            .materialized_slice(py)?
            .bind(py)
            .get_item(index)?
            .getattr(intern!(py, "value"))?
            .unbind())
    }

    fn __setitem__(
        &self,
        py: Python<'_>,
        index: &Bound<'_, PyAny>,
        value: Py<PyAny>,
    ) -> PyResult<()> {
        if !index.get_type().is(py.get_type::<PyInt>()) {
            return self
                .materialized_slice(py)?
                .bind(py)
                .get_item(index)?
                .setattr(intern!(py, "value"), value);
        }
        let number = index.extract::<isize>().map_err(|error| {
            if error.is_instance_of::<pyo3::exceptions::PyOverflowError>(py) {
                pyo3::exceptions::PyIndexError::new_err("reduction index out of range")
            } else {
                error
            }
        })?;
        let source = self
            .source
            .lock()
            .unwrap()
            .as_ref()
            .map(|source| source.clone_ref(py));
        if let Some(data) = source {
            let data = data.get();
            let number = if number < 0 {
                data.len() as isize + number
            } else {
                number
            };
            if number < 0 {
                return Err(pyo3::exceptions::PyIndexError::new_err(
                    "reduction index out of range",
                ));
            }
            return data.set_value(py, number as usize, value);
        }
        self.materialized_slice(py)?
            .bind(py)
            .get_item(index)?
            .setattr("value", value)
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Ok(source) = self.source.try_lock() {
            visit.call(source.as_ref())?;
        }
        if let Ok(slice) = self.slice.try_lock() {
            visit.call(slice.as_ref())?;
        }
        if let Ok(stack) = self.stack.try_lock() {
            visit.call(stack.as_ref())?;
        }
        Ok(())
    }

    fn __clear__(&self) {
        let previous = (
            self.source.lock().unwrap().take(),
            self.slice.lock().unwrap().take(),
            self.stack.lock().unwrap().take(),
        );
        drop(previous);
    }
}
