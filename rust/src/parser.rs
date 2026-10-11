use crate::lexer::PyToken;
use crate::symbols::{EOF_SYMBOL, ERROR_SYMBOL};
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyList, PyTuple};
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
    pub action: Vec<HashMap<String, i32>>,
    pub goto: Vec<HashMap<String, usize>>,
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
                action_vec[state] = row
                    .into_iter()
                    .filter_map(|(symbol, value)| value.map(|value| (symbol, value)))
                    .collect();
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

    #[pyo3(signature = (token_stream, reduce_cb, error_cb=None, value_funcs=None))]
    fn parse(
        &self,
        py: Python<'_>,
        token_stream: &Bound<'_, PyAny>,
        reduce_cb: &Bound<'_, PyAny>,
        error_cb: Option<&Bound<'_, PyAny>>,
        value_funcs: Option<&Bound<'_, PyList>>,
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
                    let sym_type = if let Ok(t) = item.getattr("type") {
                        t.extract::<String>()?
                    } else {
                        item.str()?.to_string()
                    };
                    let value = if let Ok(v) = item.getattr("value") {
                        v.unbind()
                    } else {
                        item.clone().unbind()
                    };
                    let lineno = item
                        .getattr("lineno")
                        .and_then(|l| l.extract::<usize>())
                        .unwrap_or(1);
                    let lexpos = item
                        .getattr("lexpos")
                        .or_else(|_| item.getattr("index"))
                        .and_then(|p| p.extract::<usize>())
                        .unwrap_or(0);
                    let endpos = item
                        .getattr("end")
                        .and_then(|e| e.extract::<usize>())
                        .unwrap_or(lexpos);
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
        let mut symstack: Vec<PyParsedSymbol> = vec![PyParsedSymbol {
            sym_type: EOF_SYMBOL.to_string(),
            value: py.None(),
            lineno: 1,
            endlineno: 1,
            lexpos: 0,
            endlexpos: 0,
        }];
        let mut lookahead = fetch_next_token()?;
        let mut error_count: usize = 0;

        loop {
            let state = *statestack.last().unwrap();
            let act = self.tables.action.get(state).and_then(|act_map| {
                act_map.get(&lookahead.sym_type).copied().or_else(|| {
                    // If not matched by type, try literal match by value string
                    if let Ok(val_str) = lookahead.value.bind(py).extract::<String>() {
                        act_map.get(&val_str).copied()
                    } else {
                        None
                    }
                })
            });

            match act {
                // Case 1: Shift (act > 0)
                Some(shift_state) if shift_state > 0 => {
                    statestack.push(shift_state as usize);
                    symstack.push(lookahead);
                    lookahead = fetch_next_token()?;
                    error_count = error_count.saturating_sub(1);
                }
                // Case 2: Reduce (act < 0)
                Some(reduce_rule) if reduce_rule < 0 => {
                    let p_num = (-reduce_rule) as usize;
                    let p_len = self.prod_lens[p_num];
                    let p_name = &self.prod_names[p_num];

                    let popped_syms: Vec<PyParsedSymbol> = if p_len > 0 {
                        statestack.truncate(statestack.len() - p_len);
                        let split_idx = symstack.len() - p_len;
                        symstack.split_off(split_idx)
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
                        let args = PyTuple::new(py, popped_syms.into_iter().map(|s| s.value))?;
                        let functions = value_funcs.ok_or_else(|| {
                            pyo3::exceptions::PyValueError::new_err(
                                "value_funcs required for value actions",
                            )
                        })?;
                        let res = functions.get_item(p_num)?.call(args, None)?;
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
                        let py_popped = PyList::new(
                            py,
                            popped_syms
                                .into_iter()
                                .map(|s| Py::new(py, s))
                                .collect::<PyResult<Vec<_>>>()?,
                        )?;
                        let py_lhs = Py::new(py, lhs_sym.clone_symbol(py))?;
                        let res = reduce_cb.call1((p_num, py_lhs, py_popped))?;
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
                    symstack.push(lhs_sym);
                }
                // Case 3: Accept (act == 0)
                Some(0) => {
                    return Ok(symstack.last().unwrap().value.clone_ref(py));
                }
                // Case 4: Syntax error / Error recovery
                _ => {
                    if error_count == 0 {
                        if let Some(err_fn) = error_cb {
                            let py_lookahead = Py::new(py, lookahead.clone_symbol(py))?;
                            let res = err_fn.call1((py_lookahead,))?;
                            if !res.is_none() {
                                return Ok(res.unbind());
                            }
                        }
                    } else {
                        if lookahead.sym_type == EOF_SYMBOL {
                            return Err(pyo3::exceptions::PySyntaxError::new_err(format!(
                                "Syntax error at EOF on line {}, pos {}",
                                lookahead.lineno, lookahead.lexpos
                            )));
                        }
                        lookahead = fetch_next_token()?;
                        continue;
                    }

                    // Search statestack backwards for state that has ERROR_SYMBOL shift
                    let mut error_state_idx: Option<usize> = None;
                    for (idx, &st) in statestack.iter().enumerate().rev() {
                        if let Some(act_map) = self.tables.action.get(st) {
                            if act_map.contains_key(ERROR_SYMBOL) {
                                error_state_idx = Some(idx);
                                break;
                            }
                        }
                    }

                    if let Some(err_idx) = error_state_idx {
                        let pop_count = (statestack.len() - 1) - err_idx;
                        if pop_count > 0 {
                            statestack.truncate(statestack.len() - pop_count);
                            symstack.truncate(symstack.len() - pop_count);
                        }

                        let top_st = *statestack.last().unwrap();
                        let err_shift = self
                            .tables
                            .action
                            .get(top_st)
                            .and_then(|m| m.get(ERROR_SYMBOL))
                            .copied();
                        if let Some(shift_st) = err_shift {
                            if shift_st > 0 {
                                statestack.push(shift_st as usize);
                                symstack.push(PyParsedSymbol {
                                    sym_type: ERROR_SYMBOL.to_string(),
                                    value: lookahead.value.clone_ref(py),
                                    lineno: lookahead.lineno,
                                    endlineno: lookahead.endlineno,
                                    lexpos: lookahead.lexpos,
                                    endlexpos: lookahead.endlexpos,
                                });
                                error_count = 3;

                                let mut recovered = false;
                                while lookahead.sym_type != EOF_SYMBOL {
                                    let curr_st = *statestack.last().unwrap();
                                    if let Some(act_map) = self.tables.action.get(curr_st) {
                                        if act_map.contains_key(&lookahead.sym_type) {
                                            recovered = true;
                                            break;
                                        }
                                        if let Ok(val_str) =
                                            lookahead.value.bind(py).extract::<String>()
                                        {
                                            if act_map.contains_key(&val_str) {
                                                recovered = true;
                                                break;
                                            }
                                        }
                                    }
                                    lookahead = fetch_next_token()?;
                                }

                                if !recovered {
                                    let curr_st = *statestack.last().unwrap();
                                    let has_act = self
                                        .tables
                                        .action
                                        .get(curr_st)
                                        .map(|m| m.contains_key(&lookahead.sym_type))
                                        .unwrap_or(false);
                                    if !has_act {
                                        return Err(pyo3::exceptions::PySyntaxError::new_err(
                                            format!(
                                            "Syntax error at {:?} (type {:?}) on line {}, pos {}",
                                            lookahead.value.bind(py),
                                            lookahead.sym_type,
                                            lookahead.lineno,
                                            lookahead.lexpos
                                        ),
                                        ));
                                    }
                                }
                                continue;
                            }
                        }
                    }

                    return Err(pyo3::exceptions::PySyntaxError::new_err(format!(
                        "Syntax error at {:?} (type {:?}) on line {}, pos {}",
                        lookahead.value.bind(py),
                        lookahead.sym_type,
                        lookahead.lineno,
                        lookahead.lexpos
                    )));
                }
            }
        }
    }
}
