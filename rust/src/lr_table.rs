use crate::grammar::Grammar;
use crate::symbols::{EOF_SYMBOL, START_SYMBOL};
use std::collections::{BTreeSet, HashMap, HashSet};

const MAXINT: usize = usize::MAX;

pub type LRItem = (usize, usize); // (production_number, dot_index)

/// Cached LR(0) transitions from a state and symbol to the target state.
type StateTransitions = HashMap<(usize, String), usize>;

struct LR0Collection {
    states: Vec<BTreeSet<LRItem>>,
    ordered_states: Vec<Vec<LRItem>>,
    transitions: StateTransitions,
}

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

    fn enter<K: std::hash::Hash + Eq + Clone>(
        x: &K,
        stack: &mut Vec<K>,
        n_map: &mut HashMap<K, usize>,
        f_map: &mut HashMap<K, HashSet<String>>,
        r_rel: &impl Fn(&K) -> Vec<K>,
        fp_func: &impl Fn(&K) -> HashSet<String>,
    ) -> (K, usize, std::vec::IntoIter<K>) {
        stack.push(x.clone());
        let d = stack.len();
        n_map.insert(x.clone(), d);
        f_map.insert(x.clone(), fp_func(x));
        (x.clone(), d, r_rel(x).into_iter())
    }

    fn propagate<K: std::hash::Hash + Eq + Clone>(
        x: &K,
        y: &K,
        n_map: &mut HashMap<K, usize>,
        f_map: &mut HashMap<K, HashSet<String>>,
    ) {
        let ny = n_map[y];
        let nx = n_map.get_mut(x).unwrap();
        *nx = (*nx).min(ny);
        let fy = f_map[y].clone();
        f_map.get_mut(x).unwrap().extend(fy);
    }

    for x in x_nodes {
        if n_map.get(x).copied().unwrap_or(0) != 0 {
            continue;
        }
        let mut frames = vec![enter(
            x, &mut stack, &mut n_map, &mut f_map, &r_rel, &fp_func,
        )];
        // Resume each parent's edge iteration only after the child finishes.
        while !frames.is_empty() {
            if let Some(child) = frames.last_mut().unwrap().2.next() {
                if n_map.get(&child).copied().unwrap_or(0) == 0 {
                    frames.push(enter(
                        &child, &mut stack, &mut n_map, &mut f_map, &r_rel, &fp_func,
                    ));
                } else {
                    propagate(&frames.last().unwrap().0, &child, &mut n_map, &mut f_map);
                }
            } else {
                let (node, depth, _) = frames.pop().unwrap();
                if n_map[&node] == depth {
                    let fx = f_map[&node].clone();
                    loop {
                        let elem = stack.pop().unwrap();
                        n_map.insert(elem.clone(), MAXINT);
                        f_map.insert(elem.clone(), fx.clone());
                        if elem == node {
                            break;
                        }
                    }
                }
                if let Some((parent, _, _)) = frames.last() {
                    propagate(parent, &node, &mut n_map, &mut f_map);
                }
            }
        }
    }

    f_map
}

impl LRTable {
    pub fn build(grammar: &mut Grammar) -> Result<Self, String> {
        // Production precedence is resolved when the rule is registered;
        // ("right", 0) is a complete result and must survive compilation.
        grammar.compute_first();
        grammar.compute_follow();

        let LR0Collection {
            states,
            ordered_states,
            transitions,
        } = Self::lr0_items(grammar);
        let lookaheads = Self::compute_lalr_lookaheads(grammar, &states, &transitions);

        let mut action: HashMap<usize, HashMap<String, Option<i32>>> = HashMap::new();
        let mut goto: HashMap<usize, HashMap<String, usize>> = HashMap::new();
        let mut sr_conflicts = Vec::new();
        let mut rr_conflicts = Vec::new();
        let mut conflicts = Vec::new();

        // Canonical sets identify states; closure order determines the sequence
        // of reference conflict decisions when a token has several candidates.
        for (st, items) in ordered_states.iter().enumerate() {
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
                    let next_state = transitions.get(&(st, sym.clone())).copied();

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

    fn lr0_closure(items: &[LRItem], grammar: &Grammar) -> Vec<LRItem> {
        let mut queue: Vec<LRItem> = items.to_vec();
        let mut added: HashSet<usize> = HashSet::new();
        let mut q_idx = 0;

        while q_idx < queue.len() {
            let (pnum, idx) = queue[q_idx];
            q_idx += 1;
            let prod = &grammar.productions[pnum];
            if idx < prod.len() {
                let sym = &prod.prod[idx];
                if let Some(pnums) = grammar.prodnames.get(sym) {
                    for &pn in pnums {
                        if added.insert(pn) {
                            queue.push((pn, 0));
                        }
                    }
                }
            }
        }
        queue
    }

    fn lr0_goto(items: &[LRItem], sym: &str, grammar: &Grammar) -> Vec<LRItem> {
        let mut next_items = Vec::new();
        for &(pnum, idx) in items {
            let prod = &grammar.productions[pnum];
            if idx < prod.len() && prod.prod[idx] == sym {
                next_items.push((pnum, idx + 1));
            }
        }
        Self::lr0_closure(&next_items, grammar)
    }

    fn lr0_items(grammar: &Grammar) -> LR0Collection {
        let start_item = (0, 0);
        let i0 = Self::lr0_closure(&[start_item], grammar);
        let initial_key: BTreeSet<LRItem> = i0.iter().copied().collect();

        let mut states = vec![initial_key.clone()];
        let mut ordered_states = vec![i0];
        let mut state_map = HashMap::new();
        state_map.insert(initial_key, 0);
        let mut transitions = HashMap::new();

        let mut i = 0;
        while i < states.len() {
            let current_state = ordered_states[i].clone();

            let mut symbols = HashSet::new();
            for &(pnum, idx) in &current_state {
                let prod = &grammar.productions[pnum];
                if idx < prod.len() {
                    symbols.insert(prod.prod[idx].clone());
                }
            }

            for sym in symbols {
                let g = Self::lr0_goto(&current_state, &sym, grammar);
                if !g.is_empty() {
                    let key: BTreeSet<LRItem> = g.iter().copied().collect();
                    let next_state = match state_map.get(&key) {
                        Some(&existing) => existing,
                        None => {
                            let new_id = states.len();
                            state_map.insert(key.clone(), new_id);
                            states.push(key);
                            ordered_states.push(g);
                            new_id
                        }
                    };
                    transitions.insert((i, sym), next_state);
                }
            }
            i += 1;
        }

        LR0Collection {
            states,
            ordered_states,
            transitions,
        }
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
        trans_map: &StateTransitions,
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
            if let Some(&j) = trans_map.get(&(st, n.clone())) {
                for &(pnum, idx) in &states[j] {
                    let prod = &grammar.productions[pnum];
                    if idx < prod.len() && grammar.terminals.contains(&prod.prod[idx]) {
                        terms.insert(prod.prod[idx].clone());
                    }
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
            if let Some(&j) = trans_map.get(&(st, n.clone())) {
                for &(pnum, idx) in &states[j] {
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

                    match trans_map.get(&(j, t.clone())) {
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

#[cfg(test)]
mod digraph_tests {
    use super::digraph;
    use std::collections::{HashMap, HashSet};

    fn fixed_point(edges: &[Vec<usize>]) -> HashMap<usize, HashSet<String>> {
        let mut labels: HashMap<_, HashSet<_>> = (0..edges.len())
            .map(|node| (node, [format!("label{node}")].into_iter().collect()))
            .collect();
        loop {
            let mut changed = false;
            for (node, children) in edges.iter().enumerate() {
                let mut reachable = labels[&node].clone();
                for child in children {
                    reachable.extend(labels[child].iter().cloned());
                }
                if reachable != labels[&node] {
                    labels.insert(node, reachable);
                    changed = true;
                }
            }
            if !changed {
                return labels;
            }
        }
    }

    #[test]
    fn matches_fixed_point_on_every_three_node_graph() {
        for mask in 0..(1 << 9) {
            let edges: Vec<Vec<_>> = (0..3)
                .map(|node| {
                    (0..3)
                        .filter(|child| mask & (1 << (node * 3 + child)) != 0)
                        .collect()
                })
                .collect();
            let actual = digraph(
                &[0, 1, 2],
                |node| edges[*node].clone(),
                |node| [format!("label{node}")].into_iter().collect(),
            );
            assert_eq!(actual, fixed_point(&edges), "edge mask {mask}");
        }
    }

    #[test]
    fn diamond_and_disconnected_components_include_successors_outside_roots() {
        let edges = vec![vec![1, 2], vec![3], vec![3], vec![], vec![5], vec![]];
        let actual = digraph(
            &[0, 4],
            |node| edges[*node].clone(),
            |node| [format!("label{node}")].into_iter().collect(),
        );
        assert_eq!(actual, fixed_point(&edges));
    }

    #[test]
    fn deep_chain_does_not_use_the_call_stack() {
        let width = 10000;
        let actual = digraph(
            &[0],
            |node| {
                if *node < width - 1 {
                    vec![node + 1]
                } else {
                    vec![]
                }
            },
            |node| {
                if *node == width - 1 {
                    ["end".to_owned()].into_iter().collect()
                } else {
                    HashSet::new()
                }
            },
        );
        assert_eq!(actual.len(), width);
        for labels in actual.values() {
            assert_eq!(labels, &HashSet::from(["end".to_owned()]));
        }
    }
}

#[cfg(test)]
mod conflict_order_tests {
    use super::LRTable;
    use crate::grammar::Grammar;
    use std::collections::HashSet;

    #[test]
    fn recursive_kernel_keeps_the_reference_closure_list() {
        let mut grammar = Grammar::new(Some(HashSet::from(["a".to_owned()])));
        grammar
            .add_production("s", vec!["s".to_owned(), "a".to_owned()], 1, None)
            .unwrap();
        grammar.add_production("s", vec![], 2, None).unwrap();
        grammar.set_start(Some("s")).unwrap();

        // Python's expansion tracker begins empty, so a recursive production
        // already present in a caller's kernel is appended once during closure.
        // State identity still removes this duplicate through its canonical key.
        let ordered = LRTable::lr0_closure(&[(1, 0)], &grammar);
        assert_eq!(ordered, [(1, 0), (1, 0), (2, 0)]);
    }

    #[test]
    fn two_reductions_and_shift_follow_reference_closure_order() {
        let terminals = ["a", "b", "HIGH"]
            .into_iter()
            .map(str::to_owned)
            .collect::<HashSet<_>>();
        let mut grammar = Grammar::new(Some(terminals));
        grammar
            .set_precedence(vec![
                ("left".to_owned(), vec!["a".to_owned()]),
                ("right".to_owned(), vec!["HIGH".to_owned()]),
            ])
            .unwrap();
        grammar.add_production("A", vec![], 1, None).unwrap();
        grammar
            .add_production("B", vec![], 2, Some("HIGH"))
            .unwrap();
        for (line, rhs) in [
            (3, vec!["A", "a"]),
            (4, vec!["B", "a"]),
            (5, vec!["a", "b"]),
        ] {
            grammar
                .add_production(
                    "s",
                    rhs.into_iter().map(str::to_owned).collect(),
                    line,
                    None,
                )
                .unwrap();
        }
        grammar.set_start(Some("s")).unwrap();

        let table = LRTable::build(&mut grammar).unwrap();
        assert_eq!(table.action[&0]["a"], Some(-2));
        let decisions: Vec<_> = table
            .sr_conflicts
            .iter()
            .filter(|(state, token, _)| *state == 0 && token == "a")
            .map(|(_, _, resolution)| resolution.as_str())
            .collect();
        assert_eq!(decisions, ["shift", "reduce"]);
        assert!(table.rr_conflicts.is_empty());
    }
}
