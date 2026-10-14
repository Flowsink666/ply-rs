use crate::lexer::PyToken;
use crate::symbols::{EOF_SYMBOL, ERROR_SYMBOL};
use pyo3::class::gc::{PyTraverseError, PyVisit};
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyList, PyString, PyStringMethods, PyTuple};
use std::collections::HashMap;
use std::sync::Arc;

#[pyclass(name = "ParsedSymbol", skip_from_py_object)]
pub struct PyParsedSymbol {
    #[pyo3(get, set)]
    pub sym_type: String,
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
            sym_type,
            value: val,
            lineno,
            endlineno,
            lexpos,
            endlexpos,
        }
    }

    #[getter(r#type)]
    fn get_type(&self) -> String {
        self.sym_type.clone()
    }

    #[setter(r#type)]
    fn set_type(&mut self, t: String) {
        self.sym_type = t;
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
    pub action: Vec<HashMap<String, Option<i32>>>,
    pub goto: Vec<HashMap<String, usize>>,
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
        let mut action_vec = vec![HashMap::new(); num_states];
        for (state, row) in action {
            if state < num_states {
                action_vec[state] = row;
            }
        }
        let mut goto_vec = vec![HashMap::new(); num_states];
        for (state, row) in goto {
            if state < num_states {
                goto_vec[state] = row;
            }
        }
        Self {
            action: action_vec,
            goto: goto_vec,
        }
    }

    fn lookup_action(&self, py: Python<'_>, state: usize, symbol: &PyParsedSymbol) -> ParserAction {
        let Some(row) = self.action.get(state) else {
            return ParserAction::Missing;
        };
        // A present None is a prohibition, so literal fallback is allowed only
        // when the token type is absent. Recovery uses this same lookup.
        let action = row.get(&symbol.sym_type).copied().or_else(|| {
            if let Ok(py_str) = symbol.value.bind(py).cast::<PyString>() {
                if let Ok(s) = py_str.to_str() {
                    return row.get(s).copied();
                }
            }
            None
        });
        match action {
            Some(Some(action)) => ParserAction::Action(action),
            Some(None) => ParserAction::Forbidden,
            None => ParserAction::Missing,
        }
    }
}

#[pyclass(name = "LRParserEngine")]
pub struct LRParserEngine {
    pub tables: Arc<ParserTables>,
    pub prod_names: Vec<String>,
    pub prod_lens: Vec<usize>,
    pub has_custom_func: Vec<bool>,
    pub value_actions: Vec<bool>,
}

#[pymethods]
impl LRParserEngine {
    #[classattr]
    #[pyo3(name = "supports_prepared_slice")]
    const SUPPORTS_PREPARED_SLICE: bool = true;

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
        Self {
            tables: Arc::new(ParserTables::new(num_states, action, goto)),
            prod_names,
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
        Self {
            tables: Arc::clone(&table.runtime),
            prod_names,
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
        Self {
            tables: Arc::clone(&engine.tables),
            prod_names,
            prod_lens,
            has_custom_func,
            value_actions,
        }
    }

    #[pyo3(signature = (token_stream, reduce_cb, error_cb=None, value_funcs=None, with_stack=false, *, prepared_slice=false))]
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
    ) -> PyResult<Py<PyAny>> {
        let mut token_iter = token_stream.try_iter()?;
        let mut eof_reached = false;

        let mut fetch_next_token = || -> PyResult<PyParsedSymbol> {
            if eof_reached {
                return Ok(PyParsedSymbol {
                    sym_type: EOF_SYMBOL.to_string(),
                    value: EOF_SYMBOL.into_pyobject(py)?.into_any().unbind(),
                    lineno: 1,
                    endlineno: 1,
                    lexpos: 0,
                    endlexpos: 0,
                });
            }
            match token_iter.next() {
                Some(item_res) => {
                    let item = item_res?;
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
                        Err(error)
                            if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) =>
                        {
                            item.str()?.to_string()
                        }
                        Err(error) => return Err(error),
                    };
                    let value = match item.getattr("value") {
                        Ok(v) => v.unbind(),
                        Err(error)
                            if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) =>
                        {
                            item.clone().unbind()
                        }
                        Err(error) => return Err(error),
                    };
                    let lineno = match item.getattr("lineno") {
                        Ok(line) => line.extract::<usize>()?,
                        Err(error)
                            if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) =>
                        {
                            1
                        }
                        Err(error) => return Err(error),
                    };
                    let lexpos = match item.getattr("lexpos") {
                        Ok(position) => position.extract::<usize>()?,
                        Err(error)
                            if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) =>
                        {
                            match item.getattr("index") {
                                Ok(index) => index.extract::<usize>()?,
                                Err(error)
                                    if error
                                        .is_instance_of::<pyo3::exceptions::PyAttributeError>(
                                            py,
                                        ) =>
                                {
                                    0
                                }
                                Err(error) => return Err(error),
                            }
                        }
                        Err(error) => return Err(error),
                    };
                    let endpos = match item.getattr("end") {
                        Ok(end) => end.extract::<usize>()?,
                        Err(error)
                            if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) =>
                        {
                            lexpos + value.bind(py).str()?.len()?
                        }
                        Err(error) => return Err(error),
                    };
                    Ok(PyParsedSymbol {
                        sym_type,
                        value,
                        lineno,
                        endlineno: lineno,
                        lexpos,
                        endlexpos: endpos,
                    })
                }
                None => {
                    eof_reached = true;
                    Ok(PyParsedSymbol {
                        sym_type: EOF_SYMBOL.to_string(),
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
                    sym_type: EOF_SYMBOL.to_string(),
                    value: py.None(),
                    lineno: 1,
                    endlineno: 1,
                    lexpos: 0,
                    endlexpos: 0,
                }],
            },
        )?;
        let mut lookahead = fetch_next_token()?;
        let mut error_count: usize = 0;
        let mut recovery_lookahead: Option<PyParsedSymbol> = None;

        loop {
            let state = *statestack.last().unwrap();
            let act = if recovery_lookahead.is_some() {
                // Internal error tokens never use literal fallback or accept.
                match self
                    .tables
                    .action
                    .get(state)
                    .and_then(|row| row.get(ERROR_SYMBOL))
                    .copied()
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
                self.tables.lookup_action(py, state, &lookahead)
            };

            match act {
                // Case 1: Shift (act > 0)
                ParserAction::Action(shift_state) if shift_state > 0 => {
                    statestack.push(shift_state as usize);
                    symstack.borrow_mut(py).symbols.push(lookahead);
                    if let Some(real_token) = recovery_lookahead.take() {
                        lookahead = real_token;
                    } else {
                        lookahead = fetch_next_token()?;
                        error_count = error_count.saturating_sub(1);
                    }
                }
                // Case 2: Reduce (act < 0)
                ParserAction::Action(reduce_rule) if reduce_rule < 0 => {
                    let p_num = (-reduce_rule) as usize;
                    let p_len = self.prod_lens[p_num];
                    let p_name = &self.prod_names[p_num];

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
                    } else if self.has_custom_func[p_num] {
                        // Match the reference runtime's default $$ = $1 before
                        // invoking a proxy action, including a None return.
                        lhs_sym.value = popped_syms
                            .first()
                            .map(|s| s.value.clone_ref(py))
                            .unwrap_or_else(|| py.None());
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
                                reduce_cb.call1((p_num, &py_lhs, py_popped, &symstack, py_slice))?
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
                    } else {
                        lhs_sym.value = popped_syms
                            .into_iter()
                            .next()
                            .map(|s| s.value)
                            .unwrap_or_else(|| py.None());
                    }

                    // Goto next state
                    let curr_state = *statestack.last().unwrap();
                    let next_state = self
                        .tables
                        .goto
                        .get(curr_state)
                        .and_then(|g_map| g_map.get(p_name).copied())
                        .ok_or_else(|| {
                            pyo3::exceptions::PyRuntimeError::new_err(format!(
                                "No GOTO entry for nonterminal {:?} from state {}",
                                p_name, curr_state
                            ))
                        })?;

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
                            if !res.is_none() {
                                return Ok(res.unbind());
                            }
                        }
                    }

                    let error_on_stack = symstack
                        .borrow(py)
                        .symbols
                        .last()
                        .is_some_and(|symbol| symbol.sym_type == ERROR_SYMBOL);
                    if error_on_stack {
                        // Discard input only while waiting for synchronization.
                        if lookahead.sym_type == EOF_SYMBOL {
                            return Err(pyo3::exceptions::PySyntaxError::new_err(format!(
                                "Syntax error at EOF on line {}, pos {}",
                                lookahead.lineno, lookahead.lexpos
                            )));
                        }
                        lookahead = fetch_next_token()?;
                        continue;
                    }

                    // The counter suppresses callbacks, not new recoveries.
                    // Re-enter the LR loop so reductions on 'error' run first.
                    let mut error_symbol = lookahead.clone_symbol(py);
                    error_symbol.sym_type = ERROR_SYMBOL.to_string();
                    recovery_lookahead = Some(lookahead);
                    lookahead = error_symbol;
                    error_count = 3;
                }
            }
        }
    }
}
