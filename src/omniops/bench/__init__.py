"""Benchmark adapters. Every adapter enters OmniOps through Slack, the production entry point (D1),
and reaches data through the SQL engine over HTTP APIs (D5), so a benchmark score measures the path
users actually get (OpsIntelligence 4.1: canaries must test the path production uses)."""
