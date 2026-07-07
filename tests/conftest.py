"""Shared test fixtures / shims.

``axm_aide.records`` imports ``axm_build`` (Genesis) at module load. The shape
tests here are deliberately Genesis-free (claim vocabulary, the caller-tag-only
invariant, proposal vocabulary, key-pool load path), so when Genesis is not
installed we register a *minimal* stub that satisfies those module-level
imports. Compilation itself is NOT faked — the stub's compiler/keygen/signing
raise if actually called, so a test can never silently "pass" against a fake
sealer.

The stub mirrors the Genesis v1 surface: exactly ONE suite ``axm-hybrid1``
(Ed25519 ‖ ML-DSA-44), secret blob 3904 B, public key 1344 B, signature 2484 B.

When Genesis IS installed the real package is used and this stub is skipped.
"""
from __future__ import annotations

import sys
import types


def _install_axm_build_stub() -> None:
    try:
        import axm_build  # noqa: F401
        return  # real Genesis present — use it
    except ImportError:
        pass

    root = types.ModuleType("axm_build")

    # ── sign — v1 hybrid suite constants ─────────────────────────────────────
    sign = types.ModuleType("axm_build.sign")
    sign.SUITE_HYBRID1 = "axm-hybrid1"
    sign.HYBRID1_SK_LEN = 3904   # ed25519 seed ‖ mldsa44 sk ‖ mldsa44 pk
    sign.HYBRID1_PK_LEN = 1344   # ed25519 pk ‖ mldsa44 pk
    sign.HYBRID1_SIG_LEN = 2484  # ed25519 sig ‖ mldsa44 sig

    def _unavailable(name):
        def _raise(*_a, **_k):  # pragma: no cover - guard, not behavior
            raise NotImplementedError(f"axm_build stub: {name} unavailable")
        return _raise

    sign.hybrid1_keygen = _unavailable("hybrid1_keygen")
    sign.hybrid1_sign = _unavailable("hybrid1_sign")
    sign.hybrid1_verify = _unavailable("hybrid1_verify")
    sign.hybrid1_public_key = _unavailable("hybrid1_public_key")

    # ── compiler_generic ─────────────────────────────────────────────────────
    comp = types.ModuleType("axm_build.compiler_generic")

    class CompilerConfig:  # minimal config carrier (v1 kwargs pass through)
        def __init__(self, **kw):
            self.__dict__.update(kw)

    comp.CompilerConfig = CompilerConfig
    comp.compile_generic_shard = _unavailable("compile_generic_shard")

    root.sign = sign
    root.compiler_generic = comp

    sys.modules["axm_build"] = root
    sys.modules["axm_build.sign"] = sign
    sys.modules["axm_build.compiler_generic"] = comp


_install_axm_build_stub()
