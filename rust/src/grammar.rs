use crate::symbols::{EOF_SYMBOL, ERROR_SYMBOL, START_SYMBOL};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Production {
    pub number: usize,
    pub name: String,
    pub prod: Vec<String>,
    pub precedence: (String, i32),
    pub line: usize,
}

impl Production {
    pub fn len(&self) -> usize {
        self.prod.len()
    }

    pub fn is_empty(&self) -> bool {
        self.prod.is_empty()
    }
}

#[derive(Clone, Debug)]
pub struct Grammar {
    pub terminals: HashSet<String>,
    pub nonterminals: HashSet<String>,
    pub productions: Vec<Production>,
    pub prodnames: HashMap<String, Vec<usize>>,
    pub precedence: HashMap<String, (String, i32)>,
    pub start: Option<String>,
    pub first: HashMap<String, HashSet<String>>,
    pub follow: HashMap<String, HashSet<String>>,
}

impl Grammar {
    pub fn new(terminals: Option<HashSet<String>>) -> Self {
        let mut terms = terminals.unwrap_or_default();
        terms.insert(EOF_SYMBOL.to_string());
        terms.insert(ERROR_SYMBOL.to_string());

        Self {
            terminals: terms,
            nonterminals: HashSet::new(),
            productions: Vec::new(),
            prodnames: HashMap::new(),
            precedence: HashMap::new(),
            start: None,
            first: HashMap::new(),
            follow: HashMap::new(),
        }
    }

    pub fn set_precedence(&mut self, prec_specs: Vec<(String, Vec<String>)>) -> Result<(), String> {
        for (level, (assoc, tokens)) in (1..).zip(prec_specs) {
            if assoc != "left" && assoc != "right" && assoc != "nonassoc" {
                return Err(format!("Invalid associativity {:?}", assoc));
            }
            for mut tok in tokens {
                if tok.len() >= 2
                    && ((tok.starts_with('\'') && tok.ends_with('\''))
                        || (tok.starts_with('"') && tok.ends_with('"')))
                {
                    tok = tok[1..tok.len() - 1].to_string();
                    self.terminals.insert(tok.clone());
                }
                self.precedence.insert(tok, (assoc.clone(), level));
            }
        }
        Ok(())
    }

    pub fn add_production_with_prec(
        &mut self,
        name: &str,
        syms: Vec<String>,
        line: usize,
        prec: Option<(String, i32)>,
    ) -> Result<usize, String> {
        if self.terminals.contains(name) {
            return Err(format!(
                "Rule name {:?} conflicts with terminal symbol",
                name
            ));
        }

        self.nonterminals.insert(name.to_string());

        // Normalize literals in RHS symbols
        let mut cleaned_syms = Vec::with_capacity(syms.len());
        for mut s in syms {
            if s.len() >= 2
                && ((s.starts_with('\'') && s.ends_with('\''))
                    || (s.starts_with('"') && s.ends_with('"')))
            {
                s = s[1..s.len() - 1].to_string();
                self.terminals.insert(s.clone());
            }
            cleaned_syms.push(s);
        }

        let prod_prec = match prec {
            Some(p) => p,
            None => {
                let mut found = ("right".to_string(), 0);
                for s in cleaned_syms.iter().rev() {
                    if self.terminals.contains(s) {
                        found = self
                            .precedence
                            .get(s)
                            .cloned()
                            .unwrap_or_else(|| ("right".to_string(), 0));
                        break;
                    }
                }
                found
            }
        };

        let pnumber = self.productions.len();
        let prod = Production {
            number: pnumber,
            name: name.to_string(),
            prod: cleaned_syms,
            precedence: prod_prec,
            line,
        };

        self.productions.push(prod);
        self.prodnames
            .entry(name.to_string())
            .or_default()
            .push(pnumber);

        Ok(pnumber)
    }

    pub fn add_production(
        &mut self,
        name: &str,
        syms: Vec<String>,
        line: usize,
        precedence_override: Option<&str>,
    ) -> Result<usize, String> {
        let prec = if let Some(mut prec_sym) = precedence_override {
            if prec_sym.len() >= 2
                && ((prec_sym.starts_with('\'') && prec_sym.ends_with('\''))
                    || (prec_sym.starts_with('"') && prec_sym.ends_with('"')))
            {
                prec_sym = &prec_sym[1..prec_sym.len() - 1];
            }
            match self.precedence.get(prec_sym) {
                Some(p) => Some(p.clone()),
                None => {
                    return Err(format!(
                        "Precedence specified for unknown symbol {:?}",
                        prec_sym
                    ))
                }
            }
        } else {
            None
        };
        self.add_production_with_prec(name, syms, line, prec)
    }

    pub fn set_start(&mut self, start_symbol: Option<&str>) -> Result<(), String> {
        let start = match start_symbol {
            Some(s) => s.to_string(),
            None => {
                if self.productions.is_empty() {
                    return Err("Cannot determine start symbol: No productions defined".to_string());
                }
                self.productions[0].name.clone()
            }
        };

        if !self.nonterminals.contains(&start) {
            return Err(format!(
                "Start symbol {:?} is not a valid nonterminal",
                start
            ));
        }

        self.start = Some(start.clone());

        // Augmented start rule: S' -> start
        let augmented = Production {
            number: 0,
            name: START_SYMBOL.to_string(),
            prod: vec![start],
            precedence: ("right".to_string(), 0),
            line: 0,
        };

        self.productions.insert(0, augmented);
        for (i, p) in self.productions.iter_mut().enumerate() {
            p.number = i;
        }

        self.nonterminals.insert(START_SYMBOL.to_string());
        self.prodnames.clear();
        for (i, p) in self.productions.iter().enumerate() {
            self.prodnames.entry(p.name.clone()).or_default().push(i);
        }

        Ok(())
    }

    pub fn compute_first(&mut self) -> &HashMap<String, HashSet<String>> {
        if !self.first.is_empty() {
            return &self.first;
        }

        let mut first: HashMap<String, HashSet<String>> = HashMap::new();
        for t in &self.terminals {
            first.insert(t.clone(), [t.clone()].into_iter().collect());
        }
        for nt in &self.nonterminals {
            first.insert(nt.clone(), HashSet::new());
        }

        let mut changed = true;
        while changed {
            changed = false;
            for p in self.productions.iter().skip(1) {
                let rhs_first = self.compute_sequence_first_internal(&p.prod, &first);
                let current = first.get_mut(&p.name).unwrap();
                let old_len = current.len();
                current.extend(rhs_first);
                if current.len() > old_len {
                    changed = true;
                }
            }
        }

        self.first = first;
        &self.first
    }

    fn compute_sequence_first_internal(
        &self,
        seq: &[String],
        first_map: &HashMap<String, HashSet<String>>,
    ) -> HashSet<String> {
        if seq.is_empty() {
            return ["".to_string()].into_iter().collect();
        }

        let mut res = HashSet::new();
        let mut all_nullable = true;

        for sym in seq {
            let sym_first = match first_map.get(sym) {
                Some(f) => f.clone(),
                None => [sym.clone()].into_iter().collect(),
            };

            for item in &sym_first {
                if !item.is_empty() {
                    res.insert(item.clone());
                }
            }

            if !sym_first.contains("") {
                all_nullable = false;
                break;
            }
        }

        if all_nullable {
            res.insert("".to_string());
        }

        res
    }

    pub fn compute_sequence_first(&self, seq: &[String]) -> HashSet<String> {
        self.compute_sequence_first_internal(seq, &self.first)
    }

    pub fn compute_follow(&mut self) -> &HashMap<String, HashSet<String>> {
        if !self.follow.is_empty() {
            return &self.follow;
        }

        self.compute_first();
        let first_map = self.first.clone();

        let mut follow: HashMap<String, HashSet<String>> = HashMap::new();
        for nt in &self.nonterminals {
            follow.insert(nt.clone(), HashSet::new());
        }

        if let Some(ref start) = self.start {
            follow
                .get_mut(start)
                .unwrap()
                .insert(EOF_SYMBOL.to_string());
        }
        follow
            .get_mut(START_SYMBOL)
            .unwrap()
            .insert(EOF_SYMBOL.to_string());

        let mut changed = true;
        while changed {
            changed = false;
            for p in &self.productions {
                for (i, sym) in p.prod.iter().enumerate() {
                    if self.nonterminals.contains(sym) {
                        let rest = &p.prod[i + 1..];
                        let rest_first = self.compute_sequence_first_internal(rest, &first_map);

                        let needs_parent = rest_first.contains("") || rest.is_empty();
                        let parent_follow = if needs_parent {
                            follow.get(&p.name).cloned().unwrap_or_default()
                        } else {
                            HashSet::new()
                        };

                        let target_follow = follow.get_mut(sym).unwrap();
                        let old_len = target_follow.len();

                        for item in &rest_first {
                            if !item.is_empty() {
                                target_follow.insert(item.clone());
                            }
                        }

                        if needs_parent {
                            target_follow.extend(parent_follow);
                        }

                        if target_follow.len() > old_len {
                            changed = true;
                        }
                    }
                }
            }
        }

        self.follow = follow;
        &self.follow
    }
}
