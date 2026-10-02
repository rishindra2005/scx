// SPDX-License-Identifier: GPL-2.0
//
// Copyright (c) 2026 Risheendra MN <rishindra.hackbox@gmail.com>
//
// scx_optima: Statistics tracking and metrics presentation

use std::io::Write;
use anyhow::Result;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct OptimaStats {
    pub greedy_wspt: u64,
    pub dp_pcore: u64,
    pub dp_shared: u64,
    pub dp_ecore: u64,
    pub bb_pruned: u64,
    pub bb_preempt: u64,
    pub direct_dispatch: u64,
    pub dsq_perf: u64,
    pub dsq_eff: u64,
    pub work_steal: u64,
    pub fallback: u64,
}

impl OptimaStats {
    pub fn format<W: Write>(&self, w: &mut W) -> Result<()> {
        writeln!(
            w,
            "[scx_optima] WSPT: {:<8} | DP (P: {:<5} S: {:<5} E: {:<5}) | B&B (Pruned: {:<5} Preempt: {:<3}) | Steal: {:<5}",
            self.greedy_wspt,
            self.dp_pcore,
            self.dp_shared,
            self.dp_ecore,
            self.bb_pruned,
            self.bb_preempt,
            self.work_steal
        )?;
        Ok(())
    }

    pub fn delta(&self, prev: &Self) -> Self {
        Self {
            greedy_wspt: self.greedy_wspt.saturating_sub(prev.greedy_wspt),
            dp_pcore: self.dp_pcore.saturating_sub(prev.dp_pcore),
            dp_shared: self.dp_shared.saturating_sub(prev.dp_shared),
            dp_ecore: self.dp_ecore.saturating_sub(prev.dp_ecore),
            bb_pruned: self.bb_pruned.saturating_sub(prev.bb_pruned),
            bb_preempt: self.bb_preempt.saturating_sub(prev.bb_preempt),
            direct_dispatch: self.direct_dispatch.saturating_sub(prev.direct_dispatch),
            dsq_perf: self.dsq_perf.saturating_sub(prev.dsq_perf),
            dsq_eff: self.dsq_eff.saturating_sub(prev.dsq_eff),
            work_steal: self.work_steal.saturating_sub(prev.work_steal),
            fallback: self.fallback.saturating_sub(prev.fallback),
        }
    }
}
