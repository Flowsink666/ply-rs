use pyo3::class::gc::{PyTraverseError, PyVisit};
use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;
use regex_automata::{meta::Regex, util::syntax, Anchored, Input};
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
    stop_requested: Cell<bool>,
    input_revision: Cell<usize>,
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
            stop_requested: Cell::new(false),
            input_revision: Cell::new(0),
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
    fn input_revision(&self) -> usize {
        self.input_revision.get()
    }

    #[getter]
    fn current_state(&self) -> String {
        self.current_state.borrow().clone()
    }

    #[getter]
    fn state_stack(&self) -> Vec<String> {
        self.state_stack.borrow().clone()
    }

    #[setter]
    fn set_state_stack(&self, states: Vec<String>) -> PyResult<()> {
        if states
            .iter()
            .any(|state| !self.states.borrow().contains_key(state))
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Undefined lexer state in stack",
            ));
        }
        *self.state_stack.borrow_mut() = states;
        Ok(())
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

    #[getter]
    fn stop_requested(&self) -> bool {
        self.stop_requested.get()
    }

    #[setter]
    fn set_stop_requested(&self, value: bool) {
        self.stop_requested.set(value);
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
            .and_modify(|state| state.kind = kind.to_string())
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
        let wrapped = if flags & 64 != 0 {
            format!("(?:{}\n)", pattern) // A trailing verbose comment must not hide ')'.
        } else {
            format!("(?:{})", pattern)
        };
        let regex = Regex::builder()
            .syntax(
                syntax::Config::new()
                    .case_insensitive(flags & 2 != 0)
                    .multi_line(flags & 8 != 0)
                    .dot_matches_new_line(flags & 16 != 0)
                    .ignore_whitespace(flags & 64 != 0),
            )
            .build(&wrapped)
            .map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Failed to compile regex {:?} for token {:?}: {}",
                    pattern, name, e
                ))
            })?;

        // Compiling a rejected rule must not leave an empty state behind.
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }

        let rule = CompiledRule {
            name: name.to_string(),
            pattern: pattern.to_string(),
            func,
            priority,
            regex,
        };

        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.rules.push(rule);
            st.rules
                .sort_by_key(|rule| std::cmp::Reverse(rule.priority));
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

    #[pyo3(signature = (state, func))]
    fn set_error_func(&self, state: &str, func: Option<Py<PyAny>>) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }
        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.error_func = func;
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
        self.stop_requested.set(false);
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
        self.input_revision
            .set(self.input_revision.get().wrapping_add(1));
    }

    fn restore_state(
        &self,
        data: String,
        pos: usize,
        lineno: usize,
        current_state: &str,
        state_stack: Vec<String>,
    ) -> PyResult<()> {
        // PyO3 converts every argument before entry; validate names before input().
        let states = self.states.borrow();
        if !states.contains_key(current_state)
            || state_stack.iter().any(|state| !states.contains_key(state))
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "Snapshot contains an undefined lexer state",
            ));
        }
        drop(states);
        self.input(data);
        self.pos.set(pos);
        self.lineno.set(lineno);
        *self.current_state.borrow_mut() = current_state.to_string();
        *self.state_stack.borrow_mut() = state_stack;
        Ok(())
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

        loop {
            let data_ref = self.data.borrow();
            let total_chars = self.length.get();
            if self.pos.get() >= total_chars {
                break;
            }
            let is_ascii = self.is_ascii.get();
            let char_indices_borrow = if !is_ascii {
                Some(self.char_indices.borrow())
            } else {
                None
            };
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
            // Anchor at the scan offset while retaining context for ^ and \b.
            let input = Input::new(data_ref.as_bytes())
                .range(byte_start..)
                .anchored(Anchored::Yes);

            // Match active rules
            let states_b = self.states.borrow();
            let state_cfg = &states_b[&curr_state];
            let is_inclusive = state_cfg.kind == "inclusive" && curr_state != "INITIAL";

            let mut matched_rule: Option<(String, usize, usize)> = None; // (name, match_len, rule_idx in state)
            let mut matched_in_initial = false;

            let initial_rules = if is_inclusive {
                &states_b["INITIAL"].rules[..]
            } else {
                &[]
            };
            let local_rules = &state_cfg.rules;
            let (mut initial_idx, mut local_idx) = (0, 0);
            // Merge the sorted lists without allocating on each scanned token.
            // INITIAL precedes local rules when priorities are equal.
            while initial_idx < initial_rules.len() || local_idx < local_rules.len() {
                let from_initial = initial_idx < initial_rules.len()
                    && (local_idx == local_rules.len()
                        || initial_rules[initial_idx].priority >= local_rules[local_idx].priority);
                let (idx, rule) = if from_initial {
                    let idx = initial_idx;
                    initial_idx += 1;
                    (idx, &initial_rules[idx])
                } else {
                    let idx = local_idx;
                    local_idx += 1;
                    (idx, &local_rules[idx])
                };
                if let Some(mat) = rule.regex.find(input.clone()) {
                    let match_len = if is_ascii {
                        mat.len()
                    } else {
                        data_ref[mat.range()].chars().count()
                    };
                    matched_rule = Some((rule.name.clone(), match_len, idx));
                    matched_in_initial = from_initial;
                    break;
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
                    // User callbacks may replace input, including Unicode indexes.
                    drop(char_indices_borrow);
                    drop(data_ref);
                    let res = func.call1(py, (&py_tok,))?;
                    if res.is_none(py) {
                        // A configuration fallback may retire us inside an action.
                        if self.stop_requested.get() {
                            return Ok(None);
                        }
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
                let input_revision = self.input_revision.get();
                drop(char_indices_borrow);
                drop(data_ref);
                let res = err_func.call1(py, (py_err_tok,))?;
                if !res.is_none(py) {
                    return Ok(Some(res));
                }
                if self.stop_requested.get() {
                    // Finish error progress on the new runtime, without replaying
                    // the handler or overwriting edits made after the handoff.
                    let owner = self
                        .callback_owner
                        .borrow()
                        .as_ref()
                        .map(|l| l.clone_ref(py));
                    if let Some(owner) = owner {
                        let owner = owner.bind(py);
                        if owner.getattr("_input_revision")?.extract::<usize>()? == input_revision
                            && owner.getattr("pos")?.extract::<usize>()? == pos_now
                        {
                            owner.call_method1("skip", (1,))?;
                        }
                    } else if self.input_revision.get() == input_revision
                        && self.pos.get() == pos_now
                    {
                        self.pos.set(pos_now + 1);
                    }
                    return Ok(None);
                }
                if self.input_revision.get() == input_revision && self.pos.get() == pos_now {
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
