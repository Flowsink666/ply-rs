pub mod grammar;
pub mod lexer;
pub mod lr_table;
pub mod parser;
pub mod reduction;
pub mod symbols;

use grammar::Grammar;
use lexer::{PyLexerEngine, PyToken};
use lr_table::LRTable;
use parser::{LRParserEngine, ParserControl, ParserTables, PyParsedSymbol, TokenCursor};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use reduction::{ProductionView, ReductionData};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

#[pyclass(name = "Grammar")]
pub struct PyGrammar {
    pub inner: Grammar,
}

#[pymethods]
impl PyGrammar {
    #[new]
    #[pyo3(signature = (terminals=None))]
    fn new(terminals: Option<Vec<String>>) -> Self {
        let set = terminals.map(|t| t.into_iter().collect::<HashSet<String>>());
        Self {
            inner: Grammar::new(set),
        }
    }

    #[pyo3(signature = (name, syms, line=0, precedence=None, prec_tuple=None))]
    fn add_production(
        &mut self,
        name: &str,
        syms: Vec<String>,
        line: usize,
        precedence: Option<&str>,
        prec_tuple: Option<(String, i32)>,
    ) -> PyResult<usize> {
        if let Some(prec) = prec_tuple {
            self.inner
                .add_production_with_prec(name, syms, line, Some(prec))
                .map_err(pyo3::exceptions::PyValueError::new_err)
        } else {
            self.inner
                .add_production(name, syms, line, precedence)
                .map_err(pyo3::exceptions::PyValueError::new_err)
        }
    }

    fn set_precedence(&mut self, prec_specs: Vec<(String, Vec<String>)>) -> PyResult<()> {
        self.inner
            .set_precedence(prec_specs)
            .map_err(pyo3::exceptions::PyValueError::new_err)
    }

    #[pyo3(signature = (start=None))]
    fn set_start(&mut self, start: Option<&str>) -> PyResult<()> {
        self.inner
            .set_start(start)
            .map_err(pyo3::exceptions::PyValueError::new_err)
    }

    #[getter]
    fn first(&self) -> HashMap<String, HashSet<String>> {
        self.inner.first.clone()
    }

    #[getter]
    fn follow(&self) -> HashMap<String, HashSet<String>> {
        self.inner.follow.clone()
    }

    fn build_table(&mut self) -> PyResult<PyLRTable> {
        let table =
            LRTable::build(&mut self.inner).map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        let runtime = Arc::new(ParserTables::new(
            table.states.len(),
            table.action.clone(),
            table.goto.clone(),
        ));
        Ok(PyLRTable {
            inner: table,
            runtime,
        })
    }
}

#[pyclass(name = "LRTable")]
pub struct PyLRTable {
    pub inner: LRTable,
    pub runtime: Arc<ParserTables>,
}

#[pymethods]
impl PyLRTable {
    #[getter]
    fn action(&self) -> HashMap<usize, HashMap<String, Option<i32>>> {
        self.inner.action.clone()
    }

    #[getter]
    fn goto(&self) -> HashMap<usize, HashMap<String, usize>> {
        self.inner.goto.clone()
    }

    #[getter]
    fn sr_conflicts(&self) -> Vec<(usize, String, String)> {
        self.inner.sr_conflicts.clone()
    }

    #[getter]
    fn rr_conflicts(&self) -> Vec<(usize, usize, usize)> {
        self.inner.rr_conflicts.clone()
    }

    #[getter]
    fn num_states(&self) -> usize {
        self.inner.states.len()
    }

    #[getter]
    fn states(&self) -> Vec<Vec<(usize, usize)>> {
        self.inner.ordered_states.clone()
    }

    #[getter]
    fn lookaheads(&self) -> HashMap<(usize, usize), Vec<String>> {
        self.inner.lookaheads.clone()
    }

    #[getter]
    fn conflicts(&self, py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
        self.inner
            .conflicts
            .iter()
            .map(|c| {
                let map = PyDict::new(py);
                map.set_item("kind", &c.kind)?;
                map.set_item("state", c.state)?;
                map.set_item("token", &c.token)?;
                map.set_item("resolution", &c.resolution)?;
                map.set_item("reason", &c.reason)?;
                map.set_item("candidates", &c.candidates)?;
                Ok(map.into_any().unbind())
            })
            .collect()
    }
}

#[pyfunction]
fn version() -> &'static str {
    "0.1.0-rust"
}

#[pymodule]
fn _core_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_class::<PyGrammar>()?;
    m.add_class::<PyLRTable>()?;
    m.add_class::<PyParsedSymbol>()?;
    m.add_class::<LRParserEngine>()?;
    m.add_class::<ParserControl>()?;
    m.add_class::<TokenCursor>()?;
    m.add_class::<ProductionView>()?;
    m.add_class::<ReductionData>()?;
    m.add_class::<PyToken>()?;
    m.add_class::<PyLexerEngine>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rightmost_terminal_without_precedence() {
        let mut g = Grammar::new(Some(
            ["A".to_string(), "B".to_string()].into_iter().collect(),
        ));
        g.set_precedence(vec![("left".to_string(), vec!["A".to_string()])])
            .unwrap();
        g.add_production("s", vec!["A".to_string(), "B".to_string()], 1, None)
            .unwrap();
        assert_eq!(g.productions[0].precedence, ("right".to_string(), 0));
    }

    #[test]
    fn test_table_preserves_resolved_zero_precedence() {
        let mut g = Grammar::new(Some(
            ["A".to_string(), "B".to_string(), "N".to_string()]
                .into_iter()
                .collect(),
        ));
        g.set_precedence(vec![("left".to_string(), vec!["A".to_string()])])
            .unwrap();
        g.add_production(
            "e",
            vec![
                "e".to_string(),
                "A".to_string(),
                "B".to_string(),
                "e".to_string(),
            ],
            1,
            None,
        )
        .unwrap();
        g.add_production("e", vec!["N".to_string()], 2, None)
            .unwrap();
        g.set_start(Some("e")).unwrap();

        let table = LRTable::build(&mut g).unwrap();
        assert_eq!(g.productions[1].precedence, ("right".to_string(), 0));
        assert!(table
            .conflicts
            .iter()
            .any(|conflict| conflict.token == "A" && conflict.resolution == "shift"));
    }

    #[test]
    fn test_table_preserves_explicit_zero_precedence_override() {
        let mut g = Grammar::new(Some(
            ["PLUS".to_string(), "N".to_string()].into_iter().collect(),
        ));
        g.set_precedence(vec![("left".to_string(), vec!["PLUS".to_string()])])
            .unwrap();
        g.add_production_with_prec(
            "e",
            vec!["e".to_string(), "PLUS".to_string(), "e".to_string()],
            1,
            Some(("right".to_string(), 0)),
        )
        .unwrap();
        g.add_production("e", vec!["N".to_string()], 2, None)
            .unwrap();
        g.set_start(Some("e")).unwrap();

        let table = LRTable::build(&mut g).unwrap();
        assert_eq!(g.productions[1].precedence, ("right".to_string(), 0));
        assert!(table
            .conflicts
            .iter()
            .any(|conflict| conflict.token == "PLUS" && conflict.resolution == "shift"));
    }

    #[test]
    fn test_runtime_table_preserves_nonassoc_prohibition() {
        let mut g = Grammar::new(Some(
            ["LT".to_string(), "N".to_string()].into_iter().collect(),
        ));
        g.set_precedence(vec![("nonassoc".to_string(), vec!["LT".to_string()])])
            .unwrap();
        g.add_production(
            "e",
            vec!["e".to_string(), "LT".to_string(), "e".to_string()],
            1,
            None,
        )
        .unwrap();
        g.add_production("e", vec!["N".to_string()], 2, None)
            .unwrap();
        g.set_start(Some("e")).unwrap();

        let table = LRTable::build(&mut g).unwrap();
        let forbidden_state = table
            .action
            .iter()
            .find_map(|(state, row)| (row.get("LT") == Some(&None)).then_some(*state))
            .expect("nonassoc conflict must forbid LT");
        let runtime = ParserTables::new(table.states.len(), table.action, table.goto);
        assert_eq!(
            runtime.lookup_type(forbidden_state, runtime.symbols.get("LT").copied()),
            Some(None)
        );
    }

    #[test]
    fn test_rust_grammar_first_follow() {
        let mut g = Grammar::new(Some(
            ["a".to_string(), "b".to_string()].into_iter().collect(),
        ));
        g.add_production("S", vec!["A".to_string(), "B".to_string()], 1, None)
            .unwrap();
        g.add_production("A", vec!["a".to_string()], 2, None)
            .unwrap();
        g.add_production("B", vec!["b".to_string()], 3, None)
            .unwrap();
        g.set_start(Some("S")).unwrap();

        let first = g.compute_first();
        assert!(first.get("A").unwrap().contains("a"));
        assert!(first.get("B").unwrap().contains("b"));
        assert!(first.get("S").unwrap().contains("a"));

        let follow = g.compute_follow();
        assert!(follow
            .get("S")
            .unwrap()
            .contains(crate::symbols::EOF_SYMBOL));
        assert!(follow.get("A").unwrap().contains("b"));
        assert!(follow
            .get("B")
            .unwrap()
            .contains(crate::symbols::EOF_SYMBOL));
    }

    #[test]
    fn test_rust_lr_table_simple() {
        let mut g = Grammar::new(Some(
            ["a".to_string(), "b".to_string()].into_iter().collect(),
        ));
        g.add_production("S", vec!["A".to_string(), "B".to_string()], 1, None)
            .unwrap();
        g.add_production("A", vec!["a".to_string()], 2, None)
            .unwrap();
        g.add_production("B", vec!["b".to_string()], 3, None)
            .unwrap();
        g.set_start(Some("S")).unwrap();

        let table = LRTable::build(&mut g).unwrap();
        assert!(!table.states.is_empty());
        assert_eq!(table.conflicts.len(), 0);
        assert!(table.action.get(&0).unwrap().get("a").is_some());
    }

    #[test]
    fn test_rust_lr_table_precedence() {
        let mut g = Grammar::new(Some(
            [
                "PLUS".to_string(),
                "TIMES".to_string(),
                "NUMBER".to_string(),
            ]
            .into_iter()
            .collect(),
        ));
        g.set_precedence(vec![
            ("left".to_string(), vec!["PLUS".to_string()]),
            ("left".to_string(), vec!["TIMES".to_string()]),
        ])
        .unwrap();
        g.add_production(
            "expr",
            vec!["expr".to_string(), "PLUS".to_string(), "expr".to_string()],
            1,
            None,
        )
        .unwrap();
        g.add_production(
            "expr",
            vec!["expr".to_string(), "TIMES".to_string(), "expr".to_string()],
            2,
            None,
        )
        .unwrap();
        g.add_production("expr", vec!["NUMBER".to_string()], 3, None)
            .unwrap();
        g.set_start(Some("expr")).unwrap();

        let table = LRTable::build(&mut g).unwrap();
        assert!(!table.conflicts.is_empty());
        for c in &table.conflicts {
            assert_eq!(c.kind, "shift/reduce");
            assert!(c.resolution == "shift" || c.resolution == "reduce");
        }
    }
}
