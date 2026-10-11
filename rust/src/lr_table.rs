use crate::grammar::Grammar;
use crate::symbols::{EOF_SYMBOL, START_SYMBOL};
use std::collections::{BTreeSet, HashMap, HashSet};

const MAXINT: usize = usize::MAX;

pub type LRItem = (usize, usize); // (production_number, dot_index)

#[derive(Clone, Debug)]
pub struct ConflictRecord {
    pub kind: String, // "shift/reduce" or "reduce/reduce"
    pub state: usize,
    pub token: String,
    pub resolution: String,
    pub reason: String,
}

#[derive(Clone, Debug)]
pub struct LRTable {
    pub action: HashMap<usize, HashMap<String, Option<i32>>>,
    pub goto: HashMap<usize, HashMap<String, usize>>,
    pub states: Vec<BTreeSet<LRItem>>,
    pub sr_conflicts: Vec<(usize, String, String)>,
    pub rr_conflicts: Vec<(usize, usize, usize)>,
    pub conflicts: Vec<ConflictRecord>,
}

fn digraph<K: std::hash::Hash + Eq + Clone>(
    x_nodes: &[K],
    r_rel: impl Fn(&K) -> Vec<K>,
    fp_func: impl Fn(&K) -> HashSet<String>,
) -> HashMap<K, HashSet<String>> {
    let mut n_map: HashMap<K, usize> = x_nodes.iter().map(|k| (k.clone(), 0)).collect();
    let mut stack: Vec<K> = Vec::new();
    let mut f_map: HashMap<K, HashSet<String>> = HashMap::new();

    fn traverse<K: std::hash::Hash + Eq + Clone>(
        x: &K,
        stack: &mut Vec<K>,
        n_map: &mut HashMap<K, usize>,
        f_map: &mut HashMap<K, HashSet<String>>,
        r_rel: &impl Fn(&K) -> Vec<K>,
        fp_func: &impl Fn(&K) -> HashSet<String>,
    ) {
        stack.push(x.clone());
        let d = stack.len();
        n_map.insert(x.clone(), d);
        f_map.insert(x.clone(), fp_func(x));

        for y in r_rel(x) {
            if n_map.get(&y).copied().unwrap_or(0) == 0 {
                traverse(&y, stack, n_map, f_map, r_rel, fp_func);
            }
            let ny = n_map.get(&y).copied().unwrap_or(MAXINT);
            let nx = n_map.get_mut(x).unwrap();
            *nx = (*nx).min(ny);

            let fy = f_map.get(&y).cloned().unwrap_or_default();
            f_map.get_mut(x).unwrap().extend(fy);
        }

        if n_map.get(x).copied() == Some(d) {
            let fx = f_map.get(x).cloned().unwrap_or_default();
            loop {
                let elem = stack.pop().unwrap();
                n_map.insert(elem.clone(), MAXINT);
                f_map.insert(elem.clone(), fx.clone());
                if &elem == x {
                    break;
                }
            }
        }
    }

    for x in x_nodes {
        if n_map.get(x).copied().unwrap_or(0) == 0 {
            traverse(x, &mut stack, &mut n_map, &mut f_map, &r_rel, &fp_func);
        }
    }

    f_map
}

impl LRTable {
    pub fn build(grammar: &mut Grammar) -> Result<Self, String> {
        // Re-evaluate production precedences if any rightmost terminal now has precedence
        for p in grammar.productions.iter_mut() {
            if p.precedence == ("right".to_string(), 0) {
                for s in p.prod.iter().rev() {
                    if let Some(prec) = grammar.precedence.get(s) {
                        p.precedence = prec.clone();
                        break;
                    }
                }
            }
        }

        grammar.compute_first();
        grammar.compute_follow();

        let (states, state_map) = Self::lr0_items(grammar);
        let lookaheads = Self::compute_lalr_lookaheads(grammar, &states, &state_map);

        let mut action: HashMap<usize, HashMap<String, Option<i32>>> = HashMap::new();
        let mut goto: HashMap<usize, HashMap<String, usize>> = HashMap::new();
        let mut sr_conflicts = Vec::new();
        let mut rr_conflicts = Vec::new();
        let mut conflicts = Vec::new();

        for (st, items) in states.iter().enumerate() {
            let mut st_action: HashMap<String, Option<i32>> = HashMap::new();
            let mut st_action_rule: HashMap<String, usize> = HashMap::new();
            let mut st_goto: HashMap<String, usize> = HashMap::new();

            for &(prod_num, lr_index) in items {
                let prod = &grammar.productions[prod_num];
                if lr_index == prod.len() {
                    // Reduction or Accept
                    if prod.name == START_SYMBOL {
                        // Accept on EOF
                        st_action.insert(EOF_SYMBOL.to_string(), Some(0));
                    } else if let Some(laheads) = lookaheads.get(&(st, prod_num)) {
                        for a in laheads {
                            let new_action = -(prod_num as i32);
                            if let Some(existing) = st_action.get(a).copied() {
                                Self::resolve_conflict(
                                    st,
                                    a,
                                    existing,
                                    new_action,
                                    prod_num,
                                    &mut st_action,
                                    &mut st_action_rule,
                                    grammar,
                                    &mut sr_conflicts,
                                    &mut rr_conflicts,
                                    &mut conflicts,
                                );
                            } else {
                                st_action.insert(a.clone(), Some(new_action));
                                st_action_rule.insert(a.clone(), prod_num);
                            }
                        }
                    }
                } else {
                    // Shift or GOTO
                    let sym = &prod.prod[lr_index];
                    let next_state = state_map.get(&Self::lr0_goto(items, sym, grammar)).copied();

                    if let Some(j) = next_state {
                        if grammar.terminals.contains(sym) {
                            let new_action = j as i32;
                            if let Some(existing) = st_action.get(sym).copied() {
                                Self::resolve_conflict(
                                    st,
                                    sym,
                                    existing,
                                    new_action,
                                    prod_num,
                                    &mut st_action,
                                    &mut st_action_rule,
                                    grammar,
                                    &mut sr_conflicts,
                                    &mut rr_conflicts,
                                    &mut conflicts,
                                );
                            } else {
                                st_action.insert(sym.clone(), Some(new_action));
                                st_action_rule.insert(sym.clone(), prod_num);
                            }
                        } else if grammar.nonterminals.contains(sym) {
                            st_goto.insert(sym.clone(), j);
                        }
                    }
                }
            }

            action.insert(st, st_action);
            goto.insert(st, st_goto);
        }

        Ok(Self {
            action,
            goto,
            states,
            sr_conflicts,
            rr_conflicts,
            conflicts,
        })
    }

    fn lr0_closure(items: &[LRItem], grammar: &Grammar) -> BTreeSet<LRItem> {
        let mut set: BTreeSet<LRItem> = items.iter().copied().collect();
        let mut added: HashSet<usize> = HashSet::new();

        let mut changed = true;
        while changed {
            changed = false;
            let current_items: Vec<LRItem> = set.iter().copied().collect();
            for (pnum, idx) in current_items {
                let prod = &grammar.productions[pnum];
                if idx < prod.len() {
                    let sym = &prod.prod[idx];
                    if let Some(pnums) = grammar.prodnames.get(sym) {
                        for &pn in pnums {
                            if !added.contains(&pn) {
                                added.insert(pn);
                                set.insert((pn, 0));
                                changed = true;
                            }
                        }
                    }
                }
            }
        }
        set
    }

    fn lr0_goto(items: &BTreeSet<LRItem>, sym: &str, grammar: &Grammar) -> BTreeSet<LRItem> {
        let mut next_items = Vec::new();
        for &(pnum, idx) in items {
            let prod = &grammar.productions[pnum];
            if idx < prod.len() && prod.prod[idx] == sym {
                next_items.push((pnum, idx + 1));
            }
        }
        Self::lr0_closure(&next_items, grammar)
    }

    fn lr0_items(grammar: &Grammar) -> (Vec<BTreeSet<LRItem>>, HashMap<BTreeSet<LRItem>, usize>) {
        let start_item = (0, 0);
        let i0 = Self::lr0_closure(&[start_item], grammar);

        let mut states = vec![i0.clone()];
        let mut state_map = HashMap::new();
        state_map.insert(i0, 0);

        let mut i = 0;
        while i < states.len() {
            let current_state = states[i].clone();
            i += 1;

            let mut symbols = HashSet::new();
            for &(pnum, idx) in &current_state {
                let prod = &grammar.productions[pnum];
                if idx < prod.len() {
                    symbols.insert(prod.prod[idx].clone());
                }
            }

            for sym in symbols {
                let g = Self::lr0_goto(&current_state, &sym, grammar);
                if !g.is_empty() && !state_map.contains_key(&g) {
                    state_map.insert(g.clone(), states.len());
                    states.push(g);
                }
            }
        }

        (states, state_map)
    }

    fn compute_nullable(grammar: &Grammar) -> HashSet<String> {
        let mut nullable = HashSet::new();
        let mut num_nullable = 0;
        loop {
            for p in grammar.productions.iter().skip(1) {
                if p.is_empty() || p.prod.iter().all(|s| nullable.contains(s)) {
                    nullable.insert(p.name.clone());
                }
            }
            if nullable.len() == num_nullable {
                break;
            }
            num_nullable = nullable.len();
        }
        nullable
    }

    fn compute_lalr_lookaheads(
        grammar: &Grammar,
        states: &[BTreeSet<LRItem>],
        state_map: &HashMap<BTreeSet<LRItem>, usize>,
    ) -> HashMap<(usize, usize), HashSet<String>> {
        let nullable = Self::compute_nullable(grammar);

        // Find nonterminal transitions (state, N)
        let mut transitions = Vec::new();
        let mut seen_transitions = HashSet::new();
        for (st_idx, items) in states.iter().enumerate() {
            for &(pnum, idx) in items {
                let prod = &grammar.productions[pnum];
                if idx < prod.len() && grammar.nonterminals.contains(&prod.prod[idx]) {
                    let trans = (st_idx, prod.prod[idx].clone());
                    if seen_transitions.insert(trans.clone()) {
                        transitions.push(trans);
                    }
                }
            }
        }

        // DR relation: DR(p, A)
        let dr_relation = |trans: &(usize, String)| -> HashSet<String> {
            let (st, ref n) = *trans;
            let mut terms = HashSet::new();
            let g = Self::lr0_goto(&states[st], n, grammar);
            for &(pnum, idx) in &g {
                let prod = &grammar.productions[pnum];
                if idx < prod.len() && grammar.terminals.contains(&prod.prod[idx]) {
                    terms.insert(prod.prod[idx].clone());
                }
            }
            if st == 0 && n == &grammar.productions[0].prod[0] {
                terms.insert(EOF_SYMBOL.to_string());
            }
            terms
        };

        // READS relation: (p, A) READS (j, C)
        let reads_relation = |trans: &(usize, String)| -> Vec<(usize, String)> {
            let (st, ref n) = *trans;
            let mut rel = Vec::new();
            let g = Self::lr0_goto(&states[st], n, grammar);
            if let Some(&j) = state_map.get(&g) {
                for &(pnum, idx) in &g {
                    let prod = &grammar.productions[pnum];
                    if idx < prod.len() && nullable.contains(&prod.prod[idx]) {
                        rel.push((j, prod.prod[idx].clone()));
                    }
                }
            }
            rel
        };

        let read_sets = digraph(&transitions, reads_relation, dr_relation);

        // LOOKBACK and INCLUDES relations
        let dtrans: HashSet<(usize, String)> = transitions.iter().cloned().collect();
        let mut lookdict: HashMap<(usize, String), Vec<(usize, usize)>> = HashMap::new();
        let mut includedict: HashMap<(usize, String), Vec<(usize, String)>> = HashMap::new();

        for &(st, ref n) in &transitions {
            let mut lookb = Vec::new();
            for &(pnum, idx) in &states[st] {
                let prod = &grammar.productions[pnum];
                if &prod.name != n {
                    continue;
                }

                let mut j = st;
                let mut curr_dot = idx;
                while curr_dot < prod.len() {
                    let t = &prod.prod[curr_dot];
                    if dtrans.contains(&(j, t.clone())) {
                        let rest = &prod.prod[curr_dot + 1..];
                        if rest.iter().all(|s| nullable.contains(s)) {
                            includedict
                                .entry((j, t.clone()))
                                .or_default()
                                .push((st, n.clone()));
                        }
                    }

                    let g = Self::lr0_goto(&states[j], t, grammar);
                    match state_map.get(&g) {
                        Some(&next_j) => j = next_j,
                        None => break,
                    }
                    curr_dot += 1;
                }

                if curr_dot == prod.len() {
                    // Item in state j where production has completed dot
                    lookb.push((j, pnum));
                }
            }
            lookdict.insert((st, n.clone()), lookb);
        }

        let includes_relation = |trans: &(usize, String)| -> Vec<(usize, String)> {
            includedict.get(trans).cloned().unwrap_or_default()
        };
        let fp_read = |trans: &(usize, String)| -> HashSet<String> {
            read_sets.get(trans).cloned().unwrap_or_default()
        };

        let follow_sets = digraph(&transitions, includes_relation, fp_read);

        // Attach lookaheads to (state, prod_num)
        let mut final_lookaheads: HashMap<(usize, usize), HashSet<String>> = HashMap::new();
        for (trans, lb_list) in lookdict {
            let f = follow_sets.get(&trans).cloned().unwrap_or_default();
            for (st, pnum) in lb_list {
                final_lookaheads
                    .entry((st, pnum))
                    .or_default()
                    .extend(f.clone());
            }
        }

        final_lookaheads
    }

    #[allow(clippy::too_many_arguments)]
    fn resolve_conflict(
        state: usize,
        tok: &str,
        existing_action: Option<i32>,
        new_action: i32,
        _new_prod_num: usize,
        st_action: &mut HashMap<String, Option<i32>>,
        st_action_rule: &mut HashMap<String, usize>,
        grammar: &Grammar,
        sr_conflicts: &mut Vec<(usize, String, String)>,
        rr_conflicts: &mut Vec<(usize, usize, usize)>,
        conflicts: &mut Vec<ConflictRecord>,
    ) {
        let existing = match existing_action {
            Some(a) => a,
            None => return,
        };

        // Case 1: Shift / Reduce conflict
        if (existing > 0 && new_action < 0) || (existing < 0 && new_action > 0) {
            let (shift_state, reduce_pnum) = if existing > 0 {
                (existing, (-new_action) as usize)
            } else {
                (new_action, (-existing) as usize)
            };

            let reduce_prod = &grammar.productions[reduce_pnum];
            let (_sprec, slevel) = grammar
                .precedence
                .get(tok)
                .cloned()
                .unwrap_or_else(|| ("right".to_string(), 0));
            let (rprec, rlevel) = &reduce_prod.precedence;

            let (resolution, reason) = if slevel < *rlevel || (slevel == *rlevel && rprec == "left")
            {
                // Favor Reduce
                st_action.insert(tok.to_string(), Some(-(reduce_pnum as i32)));
                st_action_rule.insert(tok.to_string(), reduce_pnum);
                (
                    "reduce".to_string(),
                    format!(
                        "Precedence of production {} (level {}, {}) >= token {:?} (level {})",
                        reduce_prod.name, rlevel, rprec, tok, slevel
                    ),
                )
            } else if slevel == *rlevel && rprec == "nonassoc" {
                // Non-associative: drop action
                st_action.insert(tok.to_string(), None);
                (
                    "error (nonassoc)".to_string(),
                    format!(
                        "Token {:?} and rule {} are nonassociative",
                        tok, reduce_prod.name
                    ),
                )
            } else {
                // Favor Shift
                st_action.insert(tok.to_string(), Some(shift_state));
                (
                    "shift".to_string(),
                    format!(
                        "Precedence of token {:?} (level {}) > production {} (level {})",
                        tok, slevel, reduce_prod.name, rlevel
                    ),
                )
            };

            sr_conflicts.push((state, tok.to_string(), resolution.clone()));
            conflicts.push(ConflictRecord {
                kind: "shift/reduce".to_string(),
                state,
                token: tok.to_string(),
                resolution,
                reason,
            });
        }
        // Case 2: Reduce / Reduce conflict
        else if existing < 0 && new_action < 0 {
            let pnum1 = (-existing) as usize;
            let pnum2 = (-new_action) as usize;
            let prod1 = &grammar.productions[pnum1];
            let prod2 = &grammar.productions[pnum2];

            let (chosen_pnum, rejected_pnum) = if prod1.line <= prod2.line {
                (pnum1, pnum2)
            } else {
                (pnum2, pnum1)
            };

            st_action.insert(tok.to_string(), Some(-(chosen_pnum as i32)));
            st_action_rule.insert(tok.to_string(), chosen_pnum);

            rr_conflicts.push((state, chosen_pnum, rejected_pnum));
            conflicts.push(ConflictRecord {
                kind: "reduce/reduce".to_string(),
                state,
                token: tok.to_string(),
                resolution: format!("reduce using rule {}", chosen_pnum),
                reason: format!(
                    "Rule {} defined earlier than rule {}",
                    chosen_pnum, rejected_pnum
                ),
            });
        }
    }
}
