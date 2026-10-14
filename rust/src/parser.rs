use crate::lexer::PyToken;
use crate::reduction::{ProductionView, ReductionData};
use crate::symbols::{EOF_SYMBOL, ERROR_SYMBOL};
use pyo3::class::gc::{PyTraverseError, PyVisit};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyIterator, PyList, PyString, PyStringMethods, PyTuple};
use std::cell::Cell;
use std::collections::HashMap;
use std::sync::Arc;

fn parser_error(py: Python<'_>, message: String) -> PyErr {
    match py
        .import("rply.core.production")
        .and_then(|module| module.getattr("ParserError"))
        .and_then(|class| class.call1((message,)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(error) => error,
    }
}

#[pyclass(name = "ParsedSymbol", skip_from_py_object)]
pub struct PyParsedSymbol {
    pub sym_type: Arc<str>,
    #[pyo3(get, set)]
    pub value: Py<PyAny>,
    #[pyo3(get, set)]
    pub lineno: usize,
    #[pyo3(get, set)]
    pub endlineno: usize,
    #[pyo3(get, set)]
    pub lexpos: usize,
    #[pyo3(get, set)]
    pub endlexpos: usize,
}

impl PyParsedSymbol {
    pub fn clone_symbol(&self, py: Python<'_>) -> Self {
        Self {
            sym_type: self.sym_type.clone(),
            value: self.value.clone_ref(py),
            lineno: self.lineno,
            endlineno: self.endlineno,
            lexpos: self.lexpos,
            endlexpos: self.endlexpos,
        }
    }
}

#[pymethods]
impl PyParsedSymbol {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.value)
    }

    fn __clear__(slf: &Bound<'_, Self>) {
        let previous = {
            let Ok(mut symbol) = slf.try_borrow_mut() else {
                return;
            };
            std::mem::replace(&mut symbol.value, slf.py().None())
        };
        drop(previous);
    }

    #[new]
    #[pyo3(signature = (sym_type, value=None, lineno=1, endlineno=1, lexpos=0, endlexpos=0))]
    fn new(
        py: Python<'_>,
        sym_type: String,
        value: Option<Py<PyAny>>,
        lineno: usize,
        endlineno: usize,
        lexpos: usize,
        endlexpos: usize,
    ) -> Self {
        let val = value.unwrap_or_else(|| py.None());
        Self {
            sym_type: sym_type.into(),
            value: val,
            lineno,
            endlineno,
            lexpos,
            endlexpos,
        }
    }

    #[getter(r#type)]
    fn get_type(&self) -> String {
        self.sym_type.to_string()
    }

    #[setter(r#type)]
    fn set_type(&mut self, t: String) {
        self.sym_type = t.into();
    }

    #[getter(sym_type)]
    fn get_sym_type(&self) -> String {
        self.sym_type.to_string()
    }
    #[setter(sym_type)]
    fn set_sym_type(&mut self, value: String) {
        self.sym_type = value.into();
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        format!(
            "ParsedSymbol(type={:?}, value={})",
            self.sym_type,
            self.value.bind(py)
        )
    }
}

pub struct ParserTables {
    pub action: Vec<Vec<(u32, Option<i32>)>>,
    pub goto: Vec<Vec<(u32, usize)>>,
    pub symbols: HashMap<String, u32>,
}

struct SymbolLookup {
    kind: Option<u32>,
    literal: Cell<Option<Option<u32>>>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ParserAction {
    Missing,
    Forbidden,
    Action(i32),
}

/// A live stack view for proxy actions. Symbols are converted only on access,
/// so deeply nested inputs do not copy the entire stack on every reduction.
#[pyclass]
struct ParserStack {
    symbols: Vec<PyParsedSymbol>,
}

#[pymethods]
impl ParserStack {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        for symbol in &self.symbols {
            visit.call(&symbol.value)?;
        }
        Ok(())
    }

    fn __clear__(slf: &Bound<'_, Self>) {
        let previous = {
            let Ok(mut stack) = slf.try_borrow_mut() else {
                return;
            };
            std::mem::take(&mut stack.symbols)
        };
        drop(previous);
    }

    fn __getitem__(&self, py: Python<'_>, index: isize) -> PyResult<PyParsedSymbol> {
        let len = self.symbols.len() as isize;
        let index = if index < 0 { len + index } else { index };
        if index < 0 || index >= len {
            return Err(pyo3::exceptions::PyIndexError::new_err(
                "parser stack index out of range",
            ));
        }
        Ok(self.symbols[index as usize].clone_symbol(py))
    }
}

impl ParserTables {
    pub fn new(
        num_states: usize,
        action: HashMap<usize, HashMap<String, Option<i32>>>,
        goto: HashMap<usize, HashMap<String, usize>>,
    ) -> Self {
        let names: std::collections::BTreeSet<String> = action
            .values()
            .flat_map(|row| row.keys())
            .chain(goto.values().flat_map(|row| row.keys()))
            .cloned()
            .collect();
        let symbols: HashMap<String, u32> = names
            .into_iter()
            .enumerate()
            .map(|(id, name)| (name, u32::try_from(id).expect("too many parser symbols")))
            .collect();
        let mut actions = vec![Vec::new(); num_states];
        for (state, row) in action {
            if state < num_states {
                actions[state] = row
                    .into_iter()
                    .map(|(name, action)| (symbols[&name], action))
                    .collect();
                actions[state].sort_unstable_by_key(|entry| entry.0);
            }
        }
        let mut gotos = vec![Vec::new(); num_states];
        for (state, row) in goto {
            if state < num_states {
                gotos[state] = row
                    .into_iter()
                    .map(|(name, target)| (symbols[&name], target))
                    .collect();
                gotos[state].sort_unstable_by_key(|entry| entry.0);
            }
        }
        Self {
            action: actions,
            goto: gotos,
            symbols,
        }
    }

    fn symbol_lookup(&self, symbol: &PyParsedSymbol) -> SymbolLookup {
        SymbolLookup {
            kind: self.symbols.get(symbol.sym_type.as_ref()).copied(),
            literal: Cell::new(None),
        }
    }

    pub fn lookup_type(&self, state: usize, id: Option<u32>) -> Option<Option<i32>> {
        let row = self.action.get(state)?;
        let index = row.binary_search_by_key(&id?, |entry| entry.0).ok()?;
        Some(row[index].1)
    }

    fn lookup_goto(&self, state: usize, id: Option<u32>) -> Option<usize> {
        let row = self.goto.get(state)?;
        let index = row.binary_search_by_key(&id?, |entry| entry.0).ok()?;
        Some(row[index].1)
    }

    fn lookup_action(
        &self,
        py: Python<'_>,
        state: usize,
        symbol: &PyParsedSymbol,
        ids: &SymbolLookup,
    ) -> ParserAction {
        // Missing type permits literal lookup; a present prohibition never does.
        let action = self.lookup_type(state, ids.kind).or_else(|| {
            let literal = match ids.literal.get() {
                Some(id) => id,
                None => {
                    let id = symbol
                        .value
                        .bind(py)
                        .cast::<PyString>()
                        .ok()
                        .and_then(|value| value.to_str().ok())
                        .and_then(|name| self.symbols.get(name))
                        .copied();
                    ids.literal.set(Some(id));
                    id
                }
            };
            self.lookup_type(state, literal)
        });
        match action {
            Some(Some(action)) => ParserAction::Action(action),
            Some(None) => ParserAction::Forbidden,
            None => ParserAction::Missing,
        }
    }
}

fn symbol_from_token(py: Python<'_>, item: &Bound<'_, PyAny>) -> PyResult<PyParsedSymbol> {
    if let Ok(symbol) = item.extract::<PyRef<'_, PyParsedSymbol>>() {
        return Ok(symbol.clone_symbol(py));
    }
    // Native lexer tokens already have typed fields. Preserve
    // the generic Python token protocol for all other inputs.
    if item.is_instance_of::<PyToken>() {
        let token = item.extract::<PyRef<'_, PyToken>>()?;
        return Ok(PyParsedSymbol {
            sym_type: token.sym_type.clone(),
            value: token.value.clone_ref(py),
            lineno: token.lineno,
            endlineno: token.lineno,
            lexpos: token.lexpos,
            endlexpos: token.end,
        });
    }
    let sym_type = match item.getattr("type") {
        Ok(t) => t.extract::<String>()?,
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => {
            item.str()?.to_string()
        }
        Err(error) => return Err(error),
    };
    let value = match item.getattr("value") {
        Ok(v) => v.unbind(),
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => {
            item.clone().unbind()
        }
        Err(error) => return Err(error),
    };
    let lineno = match item.getattr("lineno") {
        Ok(line) => line.extract::<usize>()?,
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => 1,
        Err(error) => return Err(error),
    };
    let lexpos = match item.getattr("lexpos") {
        Ok(position) => position.extract::<usize>()?,
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => {
            match item.getattr("index") {
                Ok(index) => index.extract::<usize>()?,
                Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => 0,
                Err(error) => return Err(error),
            }
        }
        Err(error) => return Err(error),
    };
    let endpos = match item.getattr("end") {
        Ok(end) => end.extract::<usize>()?,
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => lexpos
            .checked_add(value.bind(py).str()?.len()?)
            .ok_or_else(|| {
                pyo3::exceptions::PyOverflowError::new_err("token end exceeds the counter range")
            })?,
        Err(error) => return Err(error),
    };
    Ok(PyParsedSymbol {
        sym_type: sym_type.into(),
        value,
        lineno,
        endlineno: lineno,
        lexpos,
        endlexpos: endpos,
    })
}

#[pyclass(unsendable)]
#[derive(Default)]
pub struct ParserControl {
    error_ok: Cell<bool>,
    restart_requested: Cell<bool>,
}

#[pyclass(unsendable)]
pub struct TokenCursor {
    iterator: Option<Py<PyAny>>,
    exhausted: Cell<bool>,
}

impl TokenCursor {
    fn next_item(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        if self.exhausted.get() {
            return Ok(None);
        }
        let mut iterator = self
            .iterator
            .as_ref()
            .unwrap()
            .bind(py)
            .cast::<PyIterator>()?
            .clone();
        match iterator.next() {
            Some(item) => Ok(Some(item?.unbind())),
            None => {
                self.exhausted.set(true);
                Ok(None)
            }
        }
    }
}

#[pymethods]
impl TokenCursor {
    #[new]
    fn new(tokens: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self {
            iterator: Some(tokens.try_iter()?.into_any().unbind()),
            exhausted: Cell::new(false),
        })
    }

    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        self.next_item(py)
    }

    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(self.iterator.as_ref())
    }

    fn __clear__(slf: &Bound<'_, Self>) {
        let previous = {
            let Ok(mut cursor) = slf.try_borrow_mut() else {
                return;
            };
            cursor.exhausted.set(true);
            cursor.iterator.take()
        };
        drop(previous);
    }
}

#[pymethods]
impl ParserControl {
    #[new]
    fn new() -> Self {
        Self::default()
    }
    fn errok(&self) {
        self.error_ok.set(true);
    }
    fn restart(&self) {
        self.restart_requested.set(true);
    }
}

#[pyclass(name = "LRParserEngine")]
pub struct LRParserEngine {
    pub tables: Arc<ParserTables>,
    pub prod_names: Vec<Arc<str>>,
    pub prod_lens: Vec<usize>,
    prod_ids: Vec<Option<u32>>,
    pub has_custom_func: Vec<bool>,
    pub value_actions: Vec<bool>,
}

struct ProxyContext<'py> {
    productions: Bound<'py, PyAny>,
    factory: Bound<'py, PyAny>,
    factory_new: Bound<'py, PyAny>,
    invoke: Bound<'py, PyAny>,
    lexer: Bound<'py, PyAny>,
    parser: Bound<'py, PyAny>,
}

#[pymethods]
impl LRParserEngine {
    #[classattr]
    #[pyo3(name = "supports_prepared_slice")]
    const SUPPORTS_PREPARED_SLICE: bool = true;

    #[classattr]
    #[pyo3(name = "supports_recovery_control")]
    const SUPPORTS_RECOVERY_CONTROL: bool = true;

    #[classattr]
    #[pyo3(name = "supports_native_proxy")]
    const SUPPORTS_NATIVE_PROXY: bool = true;

    #[new]
    #[pyo3(signature = (num_states, action, goto, prod_names, prod_lens, has_custom_func, value_actions=None))]
    fn new(
        num_states: usize,
        action: HashMap<usize, HashMap<String, Option<i32>>>,
        goto: HashMap<usize, HashMap<String, usize>>,
        prod_names: Vec<String>,
        prod_lens: Vec<usize>,
        has_custom_func: Vec<bool>,
        value_actions: Option<Vec<bool>>,
    ) -> Self {
        let value_actions = value_actions.unwrap_or_else(|| vec![false; prod_names.len()]);
        let tables = Arc::new(ParserTables::new(num_states, action, goto));
        let prod_ids = prod_names
            .iter()
            .map(|name| tables.symbols.get(name).copied())
            .collect();
        Self {
            tables,
            prod_ids,
            prod_names: prod_names.into_iter().map(Arc::from).collect(),
            prod_lens,
            has_custom_func,
            value_actions,
        }
    }

    #[staticmethod]
    #[pyo3(signature = (table, prod_names, prod_lens, has_custom_func, value_actions=None))]
    fn from_table(
        table: PyRef<'_, crate::PyLRTable>,
        prod_names: Vec<String>,
        prod_lens: Vec<usize>,
        has_custom_func: Vec<bool>,
        value_actions: Option<Vec<bool>>,
    ) -> Self {
        let value_actions = value_actions.unwrap_or_else(|| vec![false; prod_names.len()]);
        let tables = Arc::clone(&table.runtime);
        let prod_ids = prod_names
            .iter()
            .map(|name| tables.symbols.get(name).copied())
            .collect();
        Self {
            tables,
            prod_ids,
            prod_names: prod_names.into_iter().map(Arc::from).collect(),
            prod_lens,
            has_custom_func,
            value_actions,
        }
    }

    #[staticmethod]
    #[pyo3(signature = (engine, prod_names, prod_lens, has_custom_func, value_actions=None))]
    fn from_engine(
        engine: PyRef<'_, Self>,
        prod_names: Vec<String>,
        prod_lens: Vec<usize>,
        has_custom_func: Vec<bool>,
        value_actions: Option<Vec<bool>>,
    ) -> Self {
        let value_actions = value_actions.unwrap_or_else(|| vec![false; prod_names.len()]);
        let tables = Arc::clone(&engine.tables);
        let prod_ids = prod_names
            .iter()
            .map(|name| tables.symbols.get(name).copied())
            .collect();
        Self {
            tables,
            prod_ids,
            prod_names: prod_names.into_iter().map(Arc::from).collect(),
            prod_lens,
            has_custom_func,
            value_actions,
        }
    }

    #[pyo3(signature = (token_stream, reduce_cb, error_cb=None, value_funcs=None, with_stack=false, *, prepared_slice=false, control=None, recovery_style="core", proxy_context=None))]
    // Keep the existing Python arguments and add only an optional internal mode.
    #[allow(clippy::too_many_arguments)]
    fn parse(
        &self,
        py: Python<'_>,
        token_stream: &Bound<'_, PyAny>,
        reduce_cb: &Bound<'_, PyAny>,
        error_cb: Option<&Bound<'_, PyAny>>,
        value_funcs: Option<&Bound<'_, PyList>>,
        with_stack: bool,
        prepared_slice: bool,
        control: Option<PyRef<'_, ParserControl>>,
        recovery_style: &str,
        proxy_context: Option<&Bound<'_, PyTuple>>,
    ) -> PyResult<Py<PyAny>> {
        if !["core", "ply", "sly"].contains(&recovery_style) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Unknown parser recovery style",
            ));
        }
        let proxy_context = proxy_context
            .map(|context| {
                if context.len() != 5 {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "proxy_context requires productions, factory, invoke, lexer and parser",
                    ));
                }
                let factory = context.get_item(1)?;
                let factory_new = factory.getattr(intern!(py, "__new__"))?;
                Ok(ProxyContext {
                    productions: context.get_item(0)?,
                    factory,
                    factory_new,
                    invoke: context.get_item(2)?,
                    lexer: context.get_item(3)?,
                    parser: context.get_item(4)?,
                })
            })
            .transpose()?;
        let native_cursor = token_stream.extract::<PyRef<'_, TokenCursor>>().ok();
        let mut token_iter = if native_cursor.is_some() {
            None
        } else {
            Some(token_stream.try_iter()?)
        };
        let mut eof_reached = false;

        let mut fetch_next_token = || -> PyResult<PyParsedSymbol> {
            if eof_reached {
                return Ok(PyParsedSymbol {
                    sym_type: Arc::from(EOF_SYMBOL),
                    value: EOF_SYMBOL.into_pyobject(py)?.into_any().unbind(),
                    lineno: 1,
                    endlineno: 1,
                    lexpos: 0,
                    endlexpos: 0,
                });
            }
            let item = if let Some(cursor) = &native_cursor {
                cursor.next_item(py)?
            } else {
                token_iter
                    .as_mut()
                    .unwrap()
                    .next()
                    .transpose()?
                    .map(Bound::unbind)
            };
            match item {
                Some(item) => symbol_from_token(py, item.bind(py)),

                None => {
                    eof_reached = true;
                    Ok(PyParsedSymbol {
                        sym_type: Arc::from(EOF_SYMBOL),
                        value: EOF_SYMBOL.into_pyobject(py)?.into_any().unbind(),
                        lineno: 1,
                        endlineno: 1,
                        lexpos: 0,
                        endlexpos: 0,
                    })
                }
            }
        };

        let mut statestack: Vec<usize> = vec![0];
        let symstack = Py::new(
            py,
            ParserStack {
                symbols: vec![PyParsedSymbol {
                    sym_type: Arc::from(EOF_SYMBOL),
                    value: py.None(),
                    lineno: 1,
                    endlineno: 1,
                    lexpos: 0,
                    endlexpos: 0,
                }],
            },
        )?;
        let mut lookahead = fetch_next_token()?;
        let mut lookup = self.tables.symbol_lookup(&lookahead);
        let mut error_count: usize = 0;
        let mut recovery_lookahead: Option<PyParsedSymbol> = None;

        loop {
            if let Some(control) = &control {
                if control.restart_requested.replace(false) {
                    statestack.clear();
                    statestack.push(0);
                    let base = PyParsedSymbol {
                        sym_type: Arc::from(EOF_SYMBOL),
                        value: py.None(),
                        lineno: 1,
                        endlineno: 1,
                        lexpos: 0,
                        endlexpos: 0,
                    };
                    let previous =
                        std::mem::replace(&mut symstack.borrow_mut(py).symbols, vec![base]);
                    // Finalizers must see the committed stack after its borrow ends.
                    drop(previous);
                    recovery_lookahead = None;
                }
                if control.error_ok.replace(false) {
                    error_count = 0;
                }
            }
            let state = *statestack.last().unwrap();
            let act = if recovery_lookahead.is_some() {
                // Internal error tokens never use literal fallback or accept.
                match self
                    .tables
                    .lookup_type(state, self.tables.symbols.get(ERROR_SYMBOL).copied())
                {
                    Some(Some(0)) => ParserAction::Missing,
                    Some(Some(action))
                        if action < 0
                            && action
                                .checked_neg()
                                .and_then(|number| self.prod_lens.get(number as usize))
                                .is_none_or(|len| *len >= statestack.len()) =>
                    {
                        ParserAction::Missing
                    }
                    Some(Some(action)) => ParserAction::Action(action),
                    Some(None) => ParserAction::Forbidden,
                    None => ParserAction::Missing,
                }
            } else {
                self.tables.lookup_action(py, state, &lookahead, &lookup)
            };

            match act {
                // Case 1: Shift (act > 0)
                ParserAction::Action(shift_state) if shift_state > 0 => {
                    if shift_state as usize >= self.tables.action.len() {
                        return Err(parser_error(
                            py,
                            format!("Invalid shift state {} from state {}", shift_state, state),
                        ));
                    }
                    statestack.push(shift_state as usize);
                    symstack.borrow_mut(py).symbols.push(lookahead);
                    if let Some(real_token) = recovery_lookahead.take() {
                        lookahead = real_token;
                        lookup = self.tables.symbol_lookup(&lookahead);
                    } else {
                        lookahead = fetch_next_token()?;
                        lookup = self.tables.symbol_lookup(&lookahead);
                        error_count = error_count.saturating_sub(1);
                    }
                }
                // Case 2: Reduce (act < 0)
                ParserAction::Action(reduce_rule) if reduce_rule < 0 => {
                    let metadata = reduce_rule.checked_neg().and_then(|number| {
                        let number = number as usize;
                        Some((
                            number,
                            *self.prod_lens.get(number)?,
                            self.prod_names.get(number)?,
                            *self.has_custom_func.get(number)?,
                        ))
                    });
                    let (p_num, p_len, p_name, has_custom_func) = metadata
                        .filter(|(_, len, _, _)| *len < statestack.len())
                        .ok_or_else(|| {
                            parser_error(
                                py,
                                format!("Invalid reduction {} in state {}", reduce_rule, state),
                            )
                        })?;

                    let popped_syms: Vec<PyParsedSymbol> = if p_len > 0 {
                        statestack.truncate(statestack.len() - p_len);
                        let mut stack = symstack.borrow_mut(py);
                        let split_idx = stack.symbols.len() - p_len;
                        stack.symbols.split_off(split_idx)
                    } else {
                        Vec::new()
                    };

                    let mut lhs_sym = PyParsedSymbol {
                        sym_type: p_name.clone(),
                        value: py.None(),
                        lineno: popped_syms.first().map(|s| s.lineno).unwrap_or(1),
                        endlineno: popped_syms.last().map(|s| s.endlineno).unwrap_or(1),
                        lexpos: popped_syms.first().map(|s| s.lexpos).unwrap_or(0),
                        endlexpos: popped_syms.last().map(|s| s.endlexpos).unwrap_or(0),
                    };

                    if self.value_actions.get(p_num).copied().unwrap_or(false) {
                        let default_value = popped_syms
                            .first()
                            .map(|s| s.value.clone_ref(py))
                            .unwrap_or_else(|| py.None());
                        let functions = value_funcs.ok_or_else(|| {
                            pyo3::exceptions::PyValueError::new_err(
                                "value_funcs required for value actions",
                            )
                        })?;
                        let function = functions.get_item(p_num)?;
                        let mut values = popped_syms.into_iter().map(|s| s.value);
                        let res = match p_len {
                            0 => function.call0()?,
                            1 => function.call1((values.next().unwrap(),))?,
                            2 => {
                                function.call1((values.next().unwrap(), values.next().unwrap()))?
                            }
                            3 => function.call1((
                                values.next().unwrap(),
                                values.next().unwrap(),
                                values.next().unwrap(),
                            ))?,
                            _ => function.call(PyTuple::new(py, values)?, None)?,
                        };
                        lhs_sym.value = if res.is_none() {
                            default_value
                        } else {
                            res.unbind()
                        };
                    } else if has_custom_func {
                        // Match the reference runtime's default $$ = $1 before
                        // invoking a proxy action, including a None return.
                        lhs_sym.value = popped_syms
                            .first()
                            .map(|s| s.value.clone_ref(py))
                            .unwrap_or_else(|| py.None());
                        let proxy_production = proxy_context
                            .as_ref()
                            .map(|context| context.productions.get_item(p_num))
                            .transpose()?;
                        let native_proxy = proxy_production
                            .as_ref()
                            .map(|production| {
                                production
                                    .getattr(intern!(py, "action_style"))?
                                    .eq(intern!(py, "values"))
                            })
                            .transpose()?
                            .is_some_and(|values| !values);
                        if native_proxy {
                            let context = proxy_context.as_ref().unwrap();
                            let production = proxy_production.unwrap();
                            let function = production.getattr(intern!(py, "func"))?;
                            let previous_lhs_value = lhs_sym.value.clone_ref(py);
                            let symbols = Py::new(py, ReductionData::new(lhs_sym, popped_syms))?;
                            let proxy = if function.is_none() {
                                None
                            } else {
                                let proxy = context.factory_new.call1((
                                    &context.factory,
                                    &symbols,
                                    &symstack,
                                ))?;
                                proxy.setattr(intern!(py, "lexer"), &context.lexer)?;
                                proxy.setattr(intern!(py, "parser"), &context.parser)?;
                                proxy.setattr(
                                    intern!(py, "_namemap"),
                                    production.getattr(intern!(py, "namemap"))?,
                                )?;
                                Some(proxy)
                            };
                            // Keep one Python action frame for traceback ownership.
                            let action_result = match &proxy {
                                Some(proxy) => {
                                    context.invoke.call1((function, proxy, &symbols))?.unbind()
                                }
                                None => py.None(),
                            };
                            let result = symbols.get().result(py, action_result)?;
                            let holds_source = proxy
                                .as_ref()
                                .map(|proxy| {
                                    Ok::<_, PyErr>(
                                        proxy
                                            .cast::<ProductionView>()?
                                            .get()
                                            .holds_source(&symbols),
                                    )
                                })
                                .transpose()?
                                .unwrap_or(false);
                            if !holds_source {
                                symbols.get().release_rhs_guard();
                            }
                            drop(proxy);
                            lhs_sym = symbols.get().lhs_snapshot(py)?;
                            drop(symbols);
                            drop(previous_lhs_value);
                            lhs_sym.value = result;
                        } else {
                            let mut items =
                                Vec::with_capacity(popped_syms.len() + usize::from(prepared_slice));
                            // Detach the RHS before creating Python objects: GC and
                            // callbacks must never run while the stack is borrowed.
                            for symbol in popped_syms {
                                items.push(Py::new(py, symbol)?);
                            }
                            // Keep an independent RHS list even in prepared mode.
                            // Actions may delete or replace slice entries; the RHS
                            // must retain its original lifetime, also in tracebacks.
                            let py_popped = PyList::new(py, &items)?;
                            // The old native LHS kept its default value alive until
                            // the callback returned. Retain that lifetime without
                            // cloning the symbol's String and position fields.
                            let previous_lhs_value = lhs_sym.value.clone_ref(py);
                            let py_lhs = Py::new(py, lhs_sym)?;
                            let res = if prepared_slice {
                                items.insert(0, py_lhs.clone_ref(py));
                                let py_slice = PyList::new(py, items)?;
                                if with_stack {
                                    reduce_cb
                                        .call1((p_num, &py_lhs, py_popped, &symstack, py_slice))?
                                } else {
                                    reduce_cb.call1((p_num, &py_lhs, py_popped, py_slice))?
                                }
                            } else if with_stack {
                                drop(items);
                                reduce_cb.call1((p_num, &py_lhs, py_popped, &symstack))?
                            } else {
                                drop(items);
                                reduce_cb.call1((p_num, &py_lhs, py_popped))?
                            };
                            // Preserve position edits performed through the action proxy.
                            lhs_sym = py_lhs.borrow(py).clone_symbol(py);
                            // Drop references only after releasing the symbol borrow:
                            // a finalizer may inspect the saved proxy's committed LHS.
                            drop(previous_lhs_value);
                            lhs_sym.value = res.unbind();
                        }
                    } else {
                        lhs_sym.value = popped_syms
                            .into_iter()
                            .next()
                            .map(|s| s.value)
                            .unwrap_or_else(|| py.None());
                    }

                    if control
                        .as_ref()
                        .is_some_and(|control| control.restart_requested.get())
                    {
                        continue;
                    }
                    // Goto next state
                    let curr_state = *statestack.last().unwrap();
                    let next_state = self
                        .tables
                        .lookup_goto(curr_state, self.prod_ids[p_num])
                        .ok_or_else(|| {
                            parser_error(
                                py,
                                format!(
                                    "No GOTO entry for nonterminal {:?} from state {}",
                                    p_name, curr_state
                                ),
                            )
                        })?;
                    if next_state >= self.tables.action.len() {
                        return Err(parser_error(
                            py,
                            format!(
                                "Invalid GOTO state {} from state {}",
                                next_state, curr_state
                            ),
                        ));
                    }

                    statestack.push(next_state);
                    symstack.borrow_mut(py).symbols.push(lhs_sym);
                }
                // Case 3: Accept (act == 0)
                ParserAction::Action(0) => {
                    return Ok(symstack
                        .borrow(py)
                        .symbols
                        .last()
                        .unwrap()
                        .value
                        .clone_ref(py));
                }
                // Case 4: Syntax error / Error recovery
                _ => {
                    if recovery_lookahead.is_some() {
                        // Reduce completed statements on the synthetic error
                        // before popping states that cannot act on it.
                        if statestack.len() > 1 {
                            statestack.pop();
                            let discarded = symstack.borrow_mut(py).symbols.pop();
                            // Dropping a value may run a Python finalizer.
                            // Release the stack borrow before that can happen.
                            drop(discarded);
                            continue;
                        }
                        lookahead = recovery_lookahead.take().unwrap();
                        return Err(pyo3::exceptions::PySyntaxError::new_err(format!(
                            "Syntax error at {:?} (type {:?}) on line {}, pos {}",
                            lookahead.value.bind(py),
                            lookahead.sym_type,
                            lookahead.lineno,
                            lookahead.lexpos
                        )));
                    }

                    if error_count == 0 {
                        if let Some(err_fn) = error_cb {
                            let py_lookahead = Py::new(py, lookahead.clone_symbol(py))?;
                            let res = err_fn.call1((py_lookahead,))?;
                            let reset = control
                                .as_ref()
                                .is_some_and(|control| control.error_ok.get());
                            let restarting = control
                                .as_ref()
                                .is_some_and(|control| control.restart_requested.get());
                            let replacement = match recovery_style {
                                "ply" => reset,
                                "sly" => !res.is_none(),
                                _ => reset || restarting,
                            };
                            if replacement {
                                if let Some(control) = &control {
                                    control.error_ok.set(false);
                                }
                                error_count = 0;
                                recovery_lookahead = None;
                                lookahead = if res.is_none() {
                                    fetch_next_token()?
                                } else {
                                    symbol_from_token(py, &res)?
                                };
                                lookup = self.tables.symbol_lookup(&lookahead);
                                continue;
                            }
                            if recovery_style == "core" && !res.is_none() {
                                return Ok(res.unbind());
                            }
                            if recovery_style != "core" && lookahead.sym_type.as_ref() == EOF_SYMBOL
                            {
                                return Ok(py.None());
                            }
                            if restarting {
                                // restart() clears the stack but does not enable
                                // PLY's replacement-token protocol; that needs
                                // errok(). Discard the offender after restarting.
                                error_count = 3;
                                lookahead = fetch_next_token()?;
                                lookup = self.tables.symbol_lookup(&lookahead);
                                continue;
                            }
                        }
                    }

                    let error_on_stack = symstack
                        .borrow(py)
                        .symbols
                        .last()
                        .is_some_and(|symbol| symbol.sym_type.as_ref() == ERROR_SYMBOL);
                    if error_on_stack {
                        // Discard input only while waiting for synchronization.
                        if lookahead.sym_type.as_ref() == EOF_SYMBOL {
                            return Err(pyo3::exceptions::PySyntaxError::new_err(format!(
                                "Syntax error at EOF on line {}, pos {}",
                                lookahead.lineno, lookahead.lexpos
                            )));
                        }
                        lookahead = fetch_next_token()?;
                        lookup = self.tables.symbol_lookup(&lookahead);
                        continue;
                    }

                    // The counter suppresses callbacks, not new recoveries.
                    // Re-enter the LR loop so reductions on 'error' run first.
                    let mut error_symbol = lookahead.clone_symbol(py);
                    error_symbol.sym_type = Arc::from(ERROR_SYMBOL);
                    recovery_lookahead = Some(lookahead);
                    lookahead = error_symbol;
                    error_count = 3;
                }
            }
        }
    }
}
