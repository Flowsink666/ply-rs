use pyo3::class::gc::{PyTraverseError, PyVisit};
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyBool, PySlice, PyString};
use regex_automata::{meta::Regex, Anchored, Input, MatchKind};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::Arc;

fn string_codepoints(text: &Bound<'_, PyString>) -> PyResult<Vec<u32>> {
    match text.to_cow() {
        Ok(text) => Ok(text.chars().map(u32::from).collect()),
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyUnicodeEncodeError>(text.py()) => {
            // Call the built-in method, bypassing str-subclass overrides. UTF-32
            // with surrogatepass preserves every Python code point separately.
            let bytes = text
                .py()
                .get_type::<PyString>()
                .call_method1("encode", (text, "utf-32-le", "surrogatepass"))?
                .extract::<Vec<u8>>()?;
            Ok(bytes
                .as_chunks::<4>()
                .0
                .iter()
                .copied()
                .map(u32::from_le_bytes)
                .collect())
        }
        Err(error) => Err(error),
    }
}

#[pyclass(name = "Token", skip_from_py_object)]
pub struct PyToken {
    pub sym_type: Arc<str>,
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
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        visit.call(&self.value)?;
        visit.call(&self.lexer)
    }

    fn __clear__(slf: &Bound<'_, Self>) {
        let previous = {
            let Ok(mut token) = slf.try_borrow_mut() else {
                return;
            };
            (
                std::mem::replace(&mut token.value, slf.py().None()),
                std::mem::replace(&mut token.lexer, slf.py().None()),
            )
        };
        // Releasing values may run Python finalizers that inspect the token.
        // Publish cleared fields and release the borrow before dropping them.
        drop(previous);
    }

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
    ) -> PyResult<Self> {
        let val_len = if let Ok(s) = value.bind(py).cast::<PyString>() {
            match s.to_cow() {
                Ok(text) => text.chars().count(),
                Err(_) => string_codepoints(s)?.len(),
            }
        } else {
            1
        };
        let end_val = match end {
            Some(end) => end,
            None => lexpos.checked_add(val_len).ok_or_else(|| {
                pyo3::exceptions::PyOverflowError::new_err("token end exceeds the counter range")
            })?,
        };
        let lexer_val = lexer.unwrap_or_else(|| py.None());

        Ok(Self {
            sym_type: tok_type.into(),
            value,
            lineno,
            lexpos,
            end: end_val,
            lexer: lexer_val,
        })
    }

    #[getter(r#type)]
    fn get_type(&self) -> String {
        self.sym_type.to_string()
    }

    #[setter(r#type)]
    fn set_type(&mut self, val: String) {
        self.sym_type = val.into();
    }

    #[getter(sym_type)]
    fn get_sym_type(&self) -> String {
        self.sym_type.to_string()
    }
    #[setter(sym_type)]
    fn set_sym_type(&mut self, value: String) {
        self.sym_type = value.into();
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

    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        if let Ok(other_token) = other.extract::<PyRef<'_, PyToken>>() {
            let eq = self.sym_type == other_token.sym_type
                && self.value.bind(py).eq(other_token.value.bind(py))?;
            return Ok(PyBool::new(py, eq).to_owned().into_any().unbind());
        }
        let token_class = py.import("ply_rs.core.tokens")?.getattr("Token")?;
        if !other.is_instance(&token_class)? {
            return Ok(py.NotImplemented());
        }
        let eq = self
            .sym_type
            .as_ref()
            .into_pyobject(py)?
            .as_any()
            .eq(other.getattr("type")?)?
            && self.value.bind(py).eq(other.getattr("value")?)?;
        Ok(PyBool::new(py, eq).to_owned().into_any().unbind())
    }
}

pub enum RuleMatcher {
    Native(Regex),
    PythonRe(Py<PyAny>),
}

pub struct CompiledRule {
    pub name: Arc<str>,
    pub pattern: String,
    pub func: Option<Py<PyAny>>,
    pub priority: i32,
    pub matcher: RuleMatcher,
    // Native rules also retain the authoritative matcher for Python strings
    // containing lone surrogates, which cannot be searched as UTF-8.
    pub python_match: Option<Py<PyAny>>,
    ascii_only: bool,
    required_prefix: String,
}

enum ScanStep {
    Native {
        regex: Regex,
        rules: Vec<Rc<CompiledRule>>,
    },
    PythonRe(Rc<CompiledRule>),
}

struct ScanPlan {
    steps: Vec<ScanStep>,
}

pub struct StateConfig {
    pub kind: String, // "inclusive" or "exclusive"
    pub rules: Vec<Rc<CompiledRule>>,
    pub ignore: Vec<u32>,
    pub error_func: Option<Py<PyAny>>,
    plans: [Option<Rc<ScanPlan>>; 2],
}

#[pyclass(name = "LexerEngine", unsendable)]
pub struct PyLexerEngine {
    states: RefCell<HashMap<String, StateConfig>>,
    data: RefCell<String>,
    // Error tokens share the immutable Python source; retain one reference per
    // input instead of copying a suffix (or the whole input) for every error.
    source_text: RefCell<Option<Py<PyString>>>,
    char_indices: RefCell<Vec<(usize, char)>>,
    surrogate_codepoints: RefCell<Vec<u32>>,
    is_ascii: Cell<bool>,
    native_input: Cell<bool>,
    length: Cell<usize>,
    pos: Cell<usize>,
    lineno: Cell<usize>,
    current_state: RefCell<Arc<str>>,
    state_stack: RefCell<Vec<String>>,
    literals: RefCell<HashSet<u32>>,
    callback_owner: RefCell<Option<Py<PyAny>>>,
    input_revision: Cell<usize>,
}

impl PyLexerEngine {
    // Return the previous source without releasing it. Callers finish their
    // whole state transition before __del__ on that source can run Python.
    fn replace_input(&self, text: &Bound<'_, PyString>) -> PyResult<Option<Py<PyString>>> {
        let source = text.clone().unbind();
        let (text, native_input, codepoints) = match text.extract::<String>() {
            Ok(text) => (text, true, Vec::new()),
            Err(error)
                if error.is_instance_of::<pyo3::exceptions::PyUnicodeEncodeError>(text.py()) =>
            {
                let codepoints = string_codepoints(text)?;
                let buffer = codepoints
                    .iter()
                    .map(|&value| char::from_u32(value).unwrap_or('\u{fffd}'))
                    .collect();
                (buffer, false, codepoints)
            }
            Err(error) => return Err(error),
        };
        self.native_input.set(native_input);
        *self.surrogate_codepoints.borrow_mut() = codepoints;
        let is_ascii = text.is_ascii();
        self.is_ascii.set(is_ascii);
        if !is_ascii {
            *self.char_indices.borrow_mut() = text.char_indices().collect();
        } else {
            self.char_indices.borrow_mut().clear();
        }

        self.length.set(text.chars().count());
        *self.data.borrow_mut() = text;
        let previous_source = self.source_text.borrow_mut().replace(source);
        self.pos.set(0);
        self.lineno.set(1);
        *self.current_state.borrow_mut() = Arc::from("INITIAL");
        self.state_stack.borrow_mut().clear();
        self.input_revision
            .set(self.input_revision.get().wrapping_add(1));
        Ok(previous_source)
    }

    fn invalidate_plans(&self) {
        for state in self.states.borrow_mut().values_mut() {
            state.plans = [None, None];
        }
    }

    fn scan_plan(&self) -> PyResult<Rc<ScanPlan>> {
        let name = self.current_state.borrow().clone();
        let ascii = self.is_ascii.get();
        let states = self.states.borrow();
        let state = states.get(name.as_ref()).ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!("Undefined lexer state {:?}", name))
        })?;
        if let Some(plan) = &state.plans[usize::from(ascii)] {
            return Ok(Rc::clone(plan));
        }
        let initial = if state.kind == "inclusive" && name.as_ref() != "INITIAL" {
            states
                .get("INITIAL")
                .map(|state| state.rules.as_slice())
                .unwrap_or(&[])
        } else {
            &[]
        };
        let local = &state.rules;
        let (mut i, mut j) = (0, 0);
        let mut rules = Vec::with_capacity(initial.len() + local.len());
        while i < initial.len() || j < local.len() {
            if i < initial.len() && (j == local.len() || initial[i].priority >= local[j].priority) {
                rules.push(Rc::clone(&initial[i]));
                i += 1;
            } else {
                rules.push(Rc::clone(&local[j]));
                j += 1;
            }
        }
        drop(states);
        let mut steps = Vec::new();
        let mut i = 0;
        while i < rules.len() {
            if !matches!(rules[i].matcher, RuleMatcher::Native(_))
                || (rules[i].ascii_only && !ascii)
            {
                steps.push(ScanStep::PythonRe(Rc::clone(&rules[i])));
                i += 1;
                continue;
            }
            let start = i;
            let priority = rules[i].priority;
            while i < rules.len()
                && rules[i].priority == priority
                && matches!(rules[i].matcher, RuleMatcher::Native(_))
                && (!rules[i].ascii_only || ascii)
            {
                i += 1;
            }
            let group = &rules[start..i];
            let patterns: Vec<&str> = group.iter().map(|rule| rule.pattern.as_str()).collect();
            let combined = if group.len() > 1 {
                Regex::builder()
                    .configure(Regex::config().match_kind(MatchKind::LeftmostFirst))
                    .build_many(&patterns)
                    .ok()
            } else {
                None
            };
            if let Some(regex) = combined {
                steps.push(ScanStep::Native {
                    regex,
                    rules: group.to_vec(),
                });
            } else {
                // Group compilation is optional; its resource limits must not
                // reject individually valid rules or change their order.
                for rule in group {
                    if let RuleMatcher::Native(regex) = &rule.matcher {
                        steps.push(ScanStep::Native {
                            regex: regex.clone(),
                            rules: vec![Rc::clone(rule)],
                        });
                    }
                }
            }
        }
        let plan = Rc::new(ScanPlan { steps });
        self.states
            .borrow_mut()
            .get_mut(name.as_ref())
            .unwrap()
            .plans[usize::from(ascii)] = Some(Rc::clone(&plan));
        Ok(plan)
    }
}

#[pymethods]
impl PyLexerEngine {
    fn __traverse__(&self, visit: PyVisit<'_>) -> Result<(), PyTraverseError> {
        if let Ok(owner) = self.callback_owner.try_borrow() {
            visit.call(owner.as_ref())?;
        }
        if let Ok(source) = self.source_text.try_borrow() {
            visit.call(source.as_ref())?;
        }
        if let Ok(states) = self.states.try_borrow() {
            for state in states.values() {
                visit.call(state.error_func.as_ref())?;
                for rule in &state.rules {
                    visit.call(rule.func.as_ref())?;
                    visit.call(rule.python_match.as_ref())?;
                    if let RuleMatcher::PythonRe(matcher) = &rule.matcher {
                        visit.call(matcher)?;
                    }
                }
            }
        }
        Ok(())
    }

    fn __clear__(slf: &Bound<'_, Self>) {
        let previous = {
            let Ok(mut lexer) = slf.try_borrow_mut() else {
                return;
            };
            let mut previous = Vec::new();
            previous.extend(lexer.callback_owner.get_mut().take());
            previous.extend(lexer.source_text.get_mut().take().map(Py::into_any));
            // A finalizer can call token() on a resurrected owner. Clearing
            // the source must also leave an empty, consistent scan buffer.
            lexer.data.get_mut().clear();
            lexer.char_indices.get_mut().clear();
            lexer.surrogate_codepoints.get_mut().clear();
            lexer.is_ascii.set(true);
            lexer.length.set(0);
            lexer.pos.set(0);
            (previous, std::mem::take(lexer.states.get_mut()))
        };
        drop(previous);
    }

    #[new]
    fn new() -> Self {
        let mut states = HashMap::new();
        states.insert(
            "INITIAL".to_string(),
            StateConfig {
                kind: "inclusive".to_string(),
                rules: Vec::new(),
                ignore: Vec::new(),
                error_func: None,
                plans: [None, None],
            },
        );

        Self {
            states: RefCell::new(states),
            data: RefCell::new(String::new()),
            source_text: RefCell::new(None),
            char_indices: RefCell::new(Vec::new()),
            surrogate_codepoints: RefCell::new(Vec::new()),
            is_ascii: Cell::new(true),
            native_input: Cell::new(true),
            length: Cell::new(0),
            pos: Cell::new(0),
            lineno: Cell::new(1),
            current_state: RefCell::new(Arc::from("INITIAL")),
            state_stack: RefCell::new(Vec::new()),
            literals: RefCell::new(HashSet::new()),
            callback_owner: RefCell::new(None),
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
    fn data(&self, py: Python<'_>) -> Py<PyString> {
        self.source_text
            .borrow()
            .as_ref()
            .map(|source| source.clone_ref(py))
            .unwrap_or_else(|| PyString::new(py, "").unbind())
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
        self.current_state.borrow().to_string()
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
        let previous = self.callback_owner.borrow_mut().replace(lexer);
        drop(previous);
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
                ignore: Vec::new(),
                error_func: None,
                plans: [None, None],
            });
        self.invalidate_plans();
        Ok(())
    }

    #[pyo3(signature = (state, name, pattern, func=None, priority=0, flags=0, *, compiled=None))]
    #[allow(clippy::too_many_arguments)]
    fn add_rule(
        &self,
        py: Python<'_>,
        state: &str,
        name: &str,
        pattern: &Bound<'_, PyString>,
        func: Option<Py<PyAny>>,
        priority: i32,
        flags: i32,
        compiled: Option<Py<PyAny>>,
    ) -> PyResult<()> {
        let compiled = match compiled {
            Some(compiled) => compiled,
            None => py
                .import("re")?
                .getattr("compile")?
                .call1((pattern, flags))?
                .unbind(),
        };
        let (native_pattern, ascii_only, required_prefix) = py
            .import("ply_rs.core.regex_support")?
            .getattr("native_pattern")?
            .call1((pattern, flags, compiled.bind(py)))?
            .extract::<(Option<String>, bool, String)>()?;
        let python_match = compiled.bind(py).getattr("match")?.unbind();
        let native = native_pattern
            .as_ref()
            .and_then(|pattern| Regex::new(pattern).ok());
        let (matcher, python_match) = match native {
            Some(regex) => (RuleMatcher::Native(regex), Some(python_match)),
            None => (RuleMatcher::PythonRe(python_match), None),
        };

        // Compiling a rejected rule must not leave an empty state behind.
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }

        let rule = CompiledRule {
            name: name.into(),
            pattern: native_pattern.unwrap_or_default(),
            func,
            priority,
            matcher,
            python_match,
            ascii_only,
            required_prefix,
        };

        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.rules.push(Rc::new(rule));
            st.rules
                .sort_by_key(|rule| std::cmp::Reverse(rule.priority));
        }
        self.invalidate_plans();
        Ok(())
    }

    #[getter]
    fn matcher_kinds(&self) -> HashMap<String, Vec<&'static str>> {
        self.states
            .borrow()
            .iter()
            .map(|(name, state)| {
                (
                    name.clone(),
                    state
                        .rules
                        .iter()
                        .map(|rule| match rule.matcher {
                            RuleMatcher::Native(_) if rule.ascii_only => "native-ascii",
                            RuleMatcher::Native(_) => "native",
                            RuleMatcher::PythonRe(_) => "python-re",
                        })
                        .collect(),
                )
            })
            .collect()
    }

    fn set_ignore(&self, state: &str, chars: &Bound<'_, PyString>) -> PyResult<()> {
        let chars = string_codepoints(chars)?;
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }
        if let Some(st) = self.states.borrow_mut().get_mut(state) {
            st.ignore = chars;
        }
        Ok(())
    }

    #[pyo3(signature = (state, func))]
    fn set_error_func(&self, state: &str, func: Option<Py<PyAny>>) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            self.add_state(state, "inclusive")?;
        }
        let previous = self
            .states
            .borrow_mut()
            .get_mut(state)
            .and_then(|st| std::mem::replace(&mut st.error_func, func));
        drop(previous);
        Ok(())
    }

    fn set_literals(&self, lits: &Bound<'_, PyAny>) -> PyResult<()> {
        let mut literals = HashSet::new();
        if let Ok(s) = lits.cast::<PyString>() {
            literals.extend(string_codepoints(s)?);
        } else {
            let iter = lits.try_iter()?;
            for item in iter {
                literals.extend(string_codepoints(item?.cast::<PyString>()?)?);
            }
        }
        // Iteration can run Python and re-enter the lexer. Commit only after
        // every item is converted, without holding a runtime borrow.
        *self.literals.borrow_mut() = literals;
        Ok(())
    }

    fn input(&self, text: &Bound<'_, PyString>) -> PyResult<()> {
        let previous_source = self.replace_input(text)?;
        // A str subclass can run __del__ when its last reference is released.
        // Commit all state and release RefCell borrows before that callback;
        // a nested input() must remain the most recent input.
        drop(previous_source);
        Ok(())
    }

    fn restore_state(
        &self,
        data: &Bound<'_, PyString>,
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
        let previous_source = self.replace_input(data)?;
        self.pos.set(pos);
        self.lineno.set(lineno);
        *self.current_state.borrow_mut() = Arc::from(current_state);
        *self.state_stack.borrow_mut() = state_stack;
        drop(previous_source);
        Ok(())
    }

    fn begin(&self, state: &str) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Undefined lexer state {:?}",
                state
            )));
        }
        *self.current_state.borrow_mut() = Arc::from(state);
        Ok(())
    }

    fn push_state(&self, state: &str) -> PyResult<()> {
        if !self.states.borrow().contains_key(state) {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Undefined lexer state {:?}",
                state
            )));
        }
        let curr = self.current_state.borrow().to_string();
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
    fn skip(&self, n: usize) -> PyResult<()> {
        let pos = self.pos.get().checked_add(n).ok_or_else(|| {
            pyo3::exceptions::PyOverflowError::new_err("position exceeds the counter range")
        })?;
        self.pos.set(pos);
        Ok(())
    }

    fn token(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        'scan: loop {
            let total_chars = self.length.get();
            if self.pos.get() >= total_chars {
                return Ok(None);
            }
            let is_ascii = self.is_ascii.get();
            let data = self.data.borrow();
            let indices = self.char_indices.borrow();
            let surrogate_codepoints = self.surrogate_codepoints.borrow();
            let state_name = self.current_state.borrow().clone();
            let states = self.states.borrow();
            let state = states.get(state_name.as_ref()).ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "Undefined lexer state {:?}",
                    state_name
                ))
            })?;
            let mut pos_now = self.pos.get();
            let mut lineno = self.lineno.get();
            while pos_now < total_chars {
                let c = if !self.native_input.get() {
                    surrogate_codepoints[pos_now]
                } else if is_ascii {
                    u32::from(data.as_bytes()[pos_now])
                } else {
                    u32::from(indices[pos_now].1)
                };
                if !state.ignore.contains(&c) {
                    break;
                }
                if c == u32::from('\n') {
                    lineno = lineno.checked_add(1).ok_or_else(|| {
                        pyo3::exceptions::PyOverflowError::new_err(
                            "line number exceeds the counter range",
                        )
                    })?;
                }
                pos_now += 1;
            }
            self.pos.set(pos_now);
            self.lineno.set(lineno);
            if pos_now >= total_chars {
                return Ok(None);
            }
            let byte_start = if is_ascii {
                pos_now
            } else {
                indices[pos_now].0
            };
            let c = if !self.native_input.get() {
                surrogate_codepoints[pos_now]
            } else if is_ascii {
                u32::from(data.as_bytes()[pos_now])
            } else {
                u32::from(indices[pos_now].1)
            };
            let error_func = state.error_func.as_ref().map(|f| f.clone_ref(py));
            let source = self
                .source_text
                .borrow()
                .as_ref()
                .expect("input has source")
                .clone_ref(py);
            let input_revision = self.input_revision.get();
            let cached_plan = state.plans[usize::from(is_ascii)].as_ref().map(Rc::clone);
            drop(states);
            drop(indices);
            drop(surrogate_codepoints);
            drop(data);
            let plan = match cached_plan {
                Some(plan) => plan,
                None => self.scan_plan()?,
            };
            let mut matched: Option<(Rc<CompiledRule>, usize, Py<PyAny>)> = None;
            for step in &plan.steps {
                match step {
                    ScanStep::Native { regex, rules } if self.native_input.get() => {
                        let data = self.data.borrow();
                        let input = Input::new(data.as_bytes())
                            .range(byte_start..)
                            .anchored(Anchored::Yes);
                        if let Some(found) = regex.find(input) {
                            let value = &data[found.range()];
                            let end = pos_now
                                + if is_ascii {
                                    value.len()
                                } else {
                                    value.chars().count()
                                };
                            matched = Some((
                                Rc::clone(&rules[found.pattern().as_usize()]),
                                end,
                                PyString::new(py, value).into_any().unbind(),
                            ));
                        }
                    }
                    _ => {
                        let rules = match step {
                            ScanStep::Native { rules, .. } => rules.as_slice(),
                            ScanStep::PythonRe(rule) => std::slice::from_ref(rule),
                        };
                        for rule in rules {
                            if self.native_input.get()
                                && !rule.required_prefix.is_empty()
                                && !self.data.borrow().as_bytes()[byte_start..]
                                    .starts_with(rule.required_prefix.as_bytes())
                            {
                                continue;
                            }
                            let matcher = match &rule.matcher {
                                RuleMatcher::PythonRe(matcher) => matcher,
                                RuleMatcher::Native(_) => rule.python_match.as_ref().unwrap(),
                            };
                            // All input/configuration borrows end before Python.
                            let found = matcher.bind(py).call1((source.bind(py), pos_now))?;
                            if !found.is_none() {
                                let end = found.call_method0("end")?.extract::<usize>()?;
                                let value = found.call_method1("group", (0,))?.unbind();
                                matched = Some((Rc::clone(rule), end, value));
                            }
                            if self.input_revision.get() != input_revision
                                || self.pos.get() != pos_now
                                || *self.current_state.borrow() != state_name
                            {
                                continue 'scan;
                            }
                            if matched.is_some() {
                                break;
                            }
                        }
                    }
                }
                if matched.is_some() {
                    break;
                }
            }
            if let Some((rule, end, value)) = matched {
                if end == pos_now {
                    let remainder = source.bind(py).get_item(PySlice::new(
                        py,
                        pos_now as isize,
                        total_chars as isize,
                        1,
                    ))?;
                    let cls = py.import("ply_rs.core.lexer_engine")?.getattr("LexError")?;
                    return Err(PyErr::from_value(cls.call1((
                        format!(
                            "Rule {:?} matched an empty string at pos {}",
                            rule.name, pos_now
                        ),
                        remainder,
                        pos_now,
                    ))?));
                }
                self.pos.set(end);
                let owner = self
                    .callback_owner
                    .borrow()
                    .as_ref()
                    .map(|owner| owner.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                let tok = Py::new(
                    py,
                    PyToken {
                        sym_type: rule.name.clone(),
                        value,
                        lineno,
                        lexpos: pos_now,
                        end,
                        lexer: owner,
                    },
                )?;
                if let Some(func) = &rule.func {
                    let result = func.call1(py, (&tok,))?;
                    if result.is_none(py) {
                        continue;
                    }
                    return Ok(Some(result));
                }
                return Ok(Some(tok.into_any()));
            }
            if self.literals.borrow().contains(&c) {
                let owner = self
                    .callback_owner
                    .borrow()
                    .as_ref()
                    .map(|owner| owner.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                self.pos.set(pos_now + 1);
                let Some(character) = char::from_u32(c) else {
                    // A surrogate cannot be a Rust string/symbol name. The
                    // public Python token can retain this rare literal exactly.
                    let value = py.import("builtins")?.getattr("chr")?.call1((c,))?;
                    return Ok(Some(
                        py.import("ply_rs.core.tokens")?
                            .getattr("Token")?
                            .call1((&value, &value, lineno, pos_now, pos_now + 1, owner))?
                            .unbind(),
                    ));
                };
                let value = character.to_string();
                return Ok(Some(
                    Py::new(
                        py,
                        PyToken {
                            sym_type: value.clone().into(),
                            value: value.into_pyobject(py)?.into_any().unbind(),
                            lineno,
                            lexpos: pos_now,
                            end: pos_now + 1,
                            lexer: owner,
                        },
                    )?
                    .into_any(),
                ));
            }
            if let Some(func) = error_func {
                let owner = self
                    .callback_owner
                    .borrow()
                    .as_ref()
                    .map(|owner| owner.clone_ref(py))
                    .unwrap_or_else(|| py.None());
                let token = py
                    .import("ply_rs.core.tokens")?
                    .getattr("_LazyErrorToken")?
                    .call1((source.bind(py), pos_now, lineno, owner, total_chars))?;
                let result = func.call1(py, (token,))?;
                if !result.is_none(py) {
                    return Ok(Some(result));
                }
                if self.input_revision.get() == input_revision && self.pos.get() == pos_now {
                    self.pos.set(pos_now + 1);
                }
                continue;
            }
            let character = char::from_u32(c)
                .map(|c| format!("{:?}", c))
                .unwrap_or_else(|| format!("'\\u{:04x}'", c));
            let message = format!(
                "Illegal character {} at line {}, pos {}",
                character, lineno, pos_now
            );
            let remainder = source.bind(py).get_item(PySlice::new(
                py,
                pos_now as isize,
                total_chars as isize,
                1,
            ))?;
            let cls = py.import("ply_rs.core.lexer_engine")?.getattr("LexError")?;
            return Err(PyErr::from_value(cls.call1((message, remainder, pos_now))?));
        }
    }

    #[pyo3(signature = (text=None))]
    fn tokenize(
        &self,
        py: Python<'_>,
        text: Option<&Bound<'_, PyString>>,
    ) -> PyResult<Vec<Py<PyAny>>> {
        if let Some(t) = text {
            self.input(t)?;
        }
        let mut tokens = Vec::new();
        while let Some(tok) = self.token(py)? {
            tokens.push(tok);
        }
        Ok(tokens)
    }
}
