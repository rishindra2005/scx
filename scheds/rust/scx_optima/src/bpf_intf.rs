// SPDX-License-Identifier: GPL-2.0
//
// scx_optima: BPF interface types inclusion

#![allow(non_upper_case_globals)]
#![allow(non_camel_case_types)]
#![allow(non_snake_case)]
#![allow(dead_code)]

include!(concat!(env!("OUT_DIR"), "/bpf_intf.rs"));
