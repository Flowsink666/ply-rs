use pyo3::class::gc::{PyTraverseError, PyVisit};
use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;
use regex::{Regex, RegexBuilder};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};

#[pyclass(name = "Token", skip_from_py_object)]
pub struct PyToken {
    #[pyo3(get, set)]
    pub sym_type: String,
    #[pyo3(get, set)]
    pub value: Py<PyAny>,
    #[pyo3(get, set)]
    pub lineno: usize,
    #[pyo3(get, set)]
    pub lexpos: usize,
    #[pyo3(get, set)]
    pub end: usize,
    #[pyo3(get, set)]
    pub lexer: Py<PyAny>,
}

#[pymethods]
impl PyToken {
    #[new]
    #[pyo3(signature = (tok_type, value, lineno=1, lexpos=0, end=None, lexer=None))]
    fn new(
        py: Python<'_>,
        tok_type: String,
        value: Py<PyAny>,
        lineno: usize,
        lexpos: usize,
        end: Option<usize>,
        lexer: Option<Py<PyAny>>,
    ) -> Self {
        let val_len = if let Ok(s) = value.bind(py).extract::<String>() {
            s.chars().count()
        } else {
            1
        };
        let end_val = end.unwrap_or(lexpos + val_len);
        let lexer_val = lexer.unwrap_or_else(|| py.None());

        Self {
            sym_type: tok_type,
            value,
            lineno,
            lexpos,
            end: end_val,
            lexer: lexer_val,
        }
    }

    #[getter(r#type)]
    fn get_type(&self) -> String {
        self.sym_type.clone()
    }

    #[setter(r#type)]
    fn set_type(&mut self, val: String) {
        self.sym_type = val;
    }

    #[getter]
    fn index(&self) -> usize {
        self.lexpos
    }

    #[setter]
    fn set_index(&mut self, val: usize) {
        self.lexpos = val;
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        format!(
            "Token(type={:?}, value={}, lineno={}, pos={})",
            self.sym_type,
            self.value.bind(py),
            self.lineno,
            self.lexpos
        )
    }

    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        if let Ok(other_token) = other.extract::<PyRef<'_, PyToken>>() {
            if self.sym_type != other_token.sym_type {
                return Ok(false);
            }
            let eq = self.value.bind(py).eq(other_token.value.bind(py))?;
            Ok(eq)
        } else {
            Ok(false)
        }
    }
}

pub struct CompiledRule {
    pub name: String,
    pub pattern: String,
    pub func: Option<Py<PyAny>>,
    pub priority: i32,
    pub regex: Regex,
}

pub struct StateConfig {
    pub kind: String, // "inclusive" or "exclusive"
    pub rules: Vec<CompiledRule>,
    pub ignore: String,
    pub error_func: Option<Py<PyAny>>,
}

#[pyclass(name = "LexerEngine", unsendable)]
pub struct PyLexerEngine {
    states: RefCell<HashMap<String, StateConfig>>,
    data: RefCell<String>,
    char_indices: RefCell<Vec<(usize, char)>>,
    is_ascii: Cell<bool>,
    length: Cell<usize>,
    pos: Cell<usize>,
    lineno: Cell<usize>,
    current_state: RefCell<String>,
    state_stack: RefCell<Vec<String>>,
    literals: RefCell<HashSet<char>>,
    callback_owner: RefCell<Option<Py<PyAny>>>,
}

#[pymethods]
impl PyLexerEngine {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Ok(owner) = self.callback_owner.try_borrow() {
            visit.call(owner.as_ref())?;
        }
        if let Ok(states) = self.states.try_borrow() {
            for state in states.values() {
                visit.call(state.error_func.as_ref())?;
                for rule in &state.rules {
                    visit.call(rule.func.as_ref())?;
                }
            }
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        self.callback_owner.get_mut().take();
        for state in self.states.get_mut().values_mut() {
            state.error_func = None;
            for rule in &mut state.rules {
                rule.func = None;
            }
        }
    }

    #[new]
    fn new() -> Self {
        let mut states = HashMap::new();
        states.insert(
            "INITIAL".to_string(),
            StateConfig {
                kind: "inclusive".to_string(),
                rules: Vec::new(),
                ignore: String::new(),
                error_func: None,
            },
        );

        Self {
            states: RefCell::new(states),
            data: RefCell::new(String::new()),
            char_indices: RefCell::new(Vec::new()),
            is_ascii: Cell::new(true),
            length: Cell::new(0),
            pos: Cell::new(0),
            lineno: Cell::new(1),
            current_state: RefCell::new("INITIAL".to_string()),
            state_stack: RefCell::new(Vec::new()),
            literals: RefCell::new(HashSet::new()),
            callback_owner: RefCell::new(None),
        }
    }

    #[getter]
    fn pos(&self) -> usize {
        self.pos.get()
    }

    #[setter]
    fn set_pos(&self, pos: usize) {
        self.pos.set(pos);
    }

    #[getter]
    fn lineno(&self) -> usize {
        self.lineno.get()
    }

    #[setter]
    fn set_lineno(&self, lineno: usize) {
        self.lineno.set(lineno);
    }

    #[getter]
    fn data(&self) -> String {
        self.data.borrow().clone()
    }

    #[getter]
    fn length(&self) -> usize {
        self.length.get()
    }

    #[getter]
    fn current_state(&self) -> String {
        self.current_state.borrow().clone()
    }

    #[getter]
    fn callback_owner(&self, py: Python<'_>) -> Py<PyAny> {
        self.callback_owner
            .borrow()
            .as_ref()
            .map(|l| l.clone_ref(py))
            .unwrap_or_else(|| py.None())
    }

    #[setter]
    fn set_callback_owner(&self, lexer: Py<PyAny>) {
        *self.callback_owner.borrow_mut() = Some(lexer);
    }

    #[pyo3(signature = (name, kind="inclusive"))]
    fn add_state(&self, name: &str, kind: &str) -> PyResult<()> {
        if kind != "inclusive" && kind != "exclusive" {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "State kind must be 'inclusive' or 'exclusive', got {:?}",
                kind
            )));
        }
        self.states
            .borrow_mut()
            .entry(name.to_string())
            .or_insert_with(|| StateConfig {
                kind: kind.to_string(),
                rules: Vec::new(),
                ignore: String::new(),
                error_func: None,
            });
        Ok(())
    }

    #[pyo3(signature = (state, name, pattern, func=None, priority=0, flags=0))]
    fn add_rule(
        &self,
        state: &str,
        name: &str,
        pattern: &str,
        func: Option<Py<PyAny>>,
        priority: i32,
        flags: i32,
    ) -> PyResult<()> {
        // Python re flags: IGNORECASE, MULTILINE, DOTALL, UNICODE, VERBOSE.
        // Reject flags without an equivalent instead of silently changing matching.
        const SUPPORTED_FLAGS: i32 = 2 | 8 | 16 | 32 | 64;
        if flags & !SUPPORTED_FLAGS != 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Unsupported Python regex flags for Rust lexer: {}",
                flags
            )));
        }
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }

        let anchored = if flags & 64 != 0 {
            format!("\\A(?:{}\n)", pattern) // A trailing verbose comment must not hide ')'.
        } else {
            format!("\\A(?:{})", pattern)
        };
        let regex = RegexBuilder::new(&anchored)
            .case_insensitive(flags & 2 != 0)
            .multi_line(flags & 8 != 0)
            .dot_matches_new_line(flags & 16 != 0)
            .ignore_whitespace(flags & 64 != 0)
            .build()
            .map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Failed to compile regex {:?} for token {:?}: {}",
                    pattern, name, e
                ))
            })?;

        let rule = CompiledRule {
            name: name.to_string(),
            pattern: pattern.to_string(),
            func,
            priority,
            regex,
        };

        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.rules.push(rule);
        }
        Ok(())
    }

    fn set_ignore(&self, state: &str, chars: &str) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }
        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.ignore = chars.to_string();
        }
        Ok(())
    }

    fn set_error_func(&self, state: &str, func: Py<PyAny>) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }
        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.error_func = Some(func);
        }
        Ok(())
    }

    fn set_literals(&self, lits: &Bound<'_, PyAny>) -> PyResult<()> {
        let mut lits_mut = self.literals.borrow_mut();
        lits_mut.clear();
        if let Ok(s) = lits.extract::<String>() {
            for c in s.chars() {
                lits_mut.insert(c);
            }
        } else if let Ok(iter) = lits.try_iter() {
            for item in iter {
                let s = item?.extract::<String>()?;
                for c in s.chars() {
                    lits_mut.insert(c);
                }
            }
        }
        Ok(())
    }

    fn input(&self, text: String) {
        let is_ascii = text.is_ascii();
        self.is_ascii.set(is_ascii);
        if !is_ascii {
            *self.char_indices.borrow_mut() = text.char_indices().collect();
        } else {
            self.char_indices.borrow_mut().clear();
        }

        self.length.set(text.chars().count());
        *self.data.borrow_mut() = text;
        self.pos.set(0);
        self.lineno.set(1);
        *self.current_state.borrow_mut() = "INITIAL".to_string();
        self.state_stack.borrow_mut().clear();
    }

    fn begin(&self, state: &str) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Undefined lexer state {:?}",
                state
            )));
        }
        *self.current_state.borrow_mut() = state.to_string();
        Ok(())
    }

    fn push_state(&self, state: &str) -> PyResult<()> {
        let curr = self.current_state.borrow().clone();
        self.state_stack.borrow_mut().push(curr);
        self.begin(state)
    }

    fn pop_state(&self) -> PyResult<()> {
        let prev = self.state_stack.borrow_mut().pop();
        if let Some(st) = prev {
            self.begin(&st)
        } else {
            Err(pyo3::exceptions::PyValueError::new_err(
                "State stack is empty",
            ))
        }
    }

    #[pyo3(signature = (n=1))]
    fn skip(&self, n: usize) {
        self.pos.set(self.pos.get() + n);
    }

    fn token(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let lex_err_cls = py.import("rply.core.lexer_engine")?.getattr("LexError")?;

        let data_ref = self.data.borrow();
        let total_chars = self.length.get();
        let is_ascii = self.is_ascii.get();
        let char_indices_borrow = if !is_ascii {
            Some(self.char_indices.borrow())
        } else {
            None
        };

        while self.pos.get() < total_chars {
            let curr_state = self.current_state.borrow().clone();
            let states_borrow = self.states.borrow();
            let state_cfg = match states_borrow.get(&curr_state) {
                Some(s) => s,
                None => {
                    return Err(pyo3::exceptions::PyValueError::new_err(format!(
                        "Undefined lexer state {:?}",
                        curr_state
                    )));
                }
            };
            let ignore_str = state_cfg.ignore.clone();
            drop(states_borrow);

            // Skip ignored characters
            let mut skipped_any = false;
            while self.pos.get() < total_chars {
                let p = self.pos.get();
                let c = if is_ascii {
                    data_ref.as_bytes()[p] as char
                } else {
                    char_indices_borrow.as_ref().unwrap()[p].1
                };

                if ignore_str.contains(c) {
                    if c == '\n' {
                        self.lineno.set(self.lineno.get() + 1);
                    }
                    self.pos.set(p + 1);
                    skipped_any = true;
                } else {
                    break;
                }
            }

            if skipped_any && self.pos.get() >= total_chars {
                break;
            }

            let pos_now = self.pos.get();
            if pos_now >= total_chars {
                break;
            }

            let byte_start = if is_ascii {
                pos_now
            } else {
                char_indices_borrow.as_ref().unwrap()[pos_now].0
            };
            let slice = &data_ref[byte_start..];

            // Match active rules
            let states_b = self.states.borrow();
            let state_cfg = &states_b[&curr_state];
            let is_inclusive = state_cfg.kind == "inclusive" && curr_state != "INITIAL";

            let mut matched_rule: Option<(String, usize, usize)> = None; // (name, match_len, rule_idx in state)
            let mut matched_in_initial = false;

            if is_inclusive {
                if let Some(init_st) = states_b.get("INITIAL") {
                    for (idx, rule) in init_st.rules.iter().enumerate() {
                        if let Some(mat) = rule.regex.find(slice) {
                            let match_len = if is_ascii {
                                mat.as_str().len()
                            } else {
                                mat.as_str().chars().count()
                            };
                            matched_rule = Some((rule.name.clone(), match_len, idx));
                            matched_in_initial = true;
                            break;
                        }
                    }
                }
            }

            if matched_rule.is_none() {
                if let Some(st) = states_b.get(&curr_state) {
                    for (idx, rule) in st.rules.iter().enumerate() {
                        if let Some(mat) = rule.regex.find(slice) {
                            let match_len = if is_ascii {
                                mat.as_str().len()
                            } else {
                                mat.as_str().chars().count()
                            };
                            matched_rule = Some((rule.name.clone(), match_len, idx));
                            matched_in_initial = false;
                            break;
                        }
                    }
                }
            }

            if let Some((rule_name, match_char_len, r_idx)) = matched_rule {
                if match_char_len == 0 {
                    drop(states_b);
                    drop(char_indices_borrow);
                    let err_inst = lex_err_cls.call1((
                        format!(
                            "Rule {:?} matched an empty string at pos {}",
                            rule_name, pos_now
                        ),
                        &data_ref[byte_start..],
                        pos_now,
                    ))?;
                    return Err(PyErr::from_value(err_inst));
                }

                let end_pos = pos_now + match_char_len;
                self.pos.set(end_pos);

                let byte_end = if is_ascii {
                    end_pos
                } else {
                    char_indices_borrow
                        .as_ref()
                        .unwrap()
                        .get(end_pos)
                        .map(|(b, _)| *b)
                        .unwrap_or(data_ref.len())
                };
                let matched_slice = &data_ref[byte_start..byte_end];
                let py_val = matched_slice.into_pyobject(py)?.into_any().unbind();
                let py_lexer_ref = self
                    .callback_owner
                    .borrow()
                    .as_ref()
                    .map(|l| l.clone_ref(py))
                    .unwrap_or_else(|| py.None());

                let tok = PyToken {
                    sym_type: rule_name,
                    value: py_val,
                    lineno: self.lineno.get(),
                    lexpos: pos_now,
                    end: end_pos,
                    lexer: py_lexer_ref,
                };
                let py_tok = Py::new(py, tok)?;

                // Extract action func
                let target_state_name = if matched_in_initial {
                    "INITIAL"
                } else {
                    &curr_state
                };
                let func_opt = states_b[target_state_name].rules[r_idx]
                    .func
                    .as_ref()
                    .map(|f| f.clone_ref(py));
                drop(states_b);

                if let Some(func) = func_opt {
                    let res = func.call1(py, (&py_tok,))?;
                    if res.is_none(py) {
                        continue;
                    }
                    return Ok(Some(res));
                } else {
                    return Ok(Some(py_tok.into_any()));
                }
            }

            let c = if is_ascii {
                data_ref.as_bytes()[pos_now] as char
            } else {
                char_indices_borrow.as_ref().unwrap()[pos_now].1
            };
            let is_literal = self.literals.borrow().contains(&c);
            let error_func_opt = states_b
                .get(&curr_state)
                .and_then(|s| s.error_func.as_ref().map(|f| f.clone_ref(py)));
            drop(states_b);

            if is_literal {
                let char_str = c.to_string();
                let py_val = char_str.clone().into_pyobject(py)?.into_any().unbind();
                let py_lexer_ref = self
                    .callback_owner
                    .borrow()
                    .as_ref()
                    .map(|l| l.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                self.pos.set(pos_now + 1);

                let tok = PyToken {
                    sym_type: char_str,
                    value: py_val,
                    lineno: self.lineno.get(),
                    lexpos: pos_now,
                    end: pos_now + 1,
                    lexer: py_lexer_ref,
                };
                return Ok(Some(Py::new(py, tok)?.into_any()));
            }

            if let Some(err_func) = error_func_opt {
                let remainder = &data_ref[byte_start..];
                let py_rem = remainder.into_pyobject(py)?.into_any().unbind();
                let py_lexer_ref = self
                    .callback_owner
                    .borrow()
                    .as_ref()
                    .map(|l| l.clone_ref(py))
                    .unwrap_or_else(|| py.None());

                let err_tok = PyToken {
                    sym_type: "error".to_string(),
                    value: py_rem,
                    lineno: self.lineno.get(),
                    lexpos: pos_now,
                    end: total_chars,
                    lexer: py_lexer_ref,
                };
                let py_err_tok = Py::new(py, err_tok)?;
                let res = err_func.call1(py, (py_err_tok,))?;
                if !res.is_none(py) {
                    return Ok(Some(res));
                }
                if self.pos.get() == pos_now {
                    self.pos.set(pos_now + 1);
                }
                continue;
            }

            drop(char_indices_borrow);
            // Unhandled lexical error
            let err_inst = lex_err_cls.call1((
                format!(
                    "Illegal character {:?} at line {}, pos {}",
                    c,
                    self.lineno.get(),
                    pos_now
                ),
                &data_ref[byte_start..],
                pos_now,
            ))?;
            return Err(PyErr::from_value(err_inst));
        }

        Ok(None)
    }

    #[pyo3(signature = (text=None))]
    fn tokenize(&self, py: Python<'_>, text: Option<String>) -> PyResult<Vec<Py<PyAny>>> {
        if let Some(t) = text {
            self.input(t);
        }
        let mut tokens = Vec::new();
        while let Some(tok) = self.token(py)? {
            tokens.push(tok);
        }
        Ok(tokens)
    }
}
