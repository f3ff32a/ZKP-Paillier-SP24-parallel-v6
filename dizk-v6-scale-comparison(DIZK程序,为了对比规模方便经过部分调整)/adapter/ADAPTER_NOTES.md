# DIZK comparison adapter

`comparison.DizkComparison` uses the pinned author's Groth16 prover and verifier,
and adapted copies of its complete setup classes, with BN254b. Author source
files remain byte-identical. The adapter
imports the complete `dizk-r1cs-v2` relation produced by the separate non-native
integer compiler. It rejects a native Paillier `Z_(N^2)` export or any different
scalar modulus; it never reduces an incompatible circuit into BN254b.

Formal input files are `header.json`, `constraints.tsv`, and `assignment.tsv`.
The header uses `variables`, `constraints`, and `primary_inputs`, including
index 0 (constant one) in the public prefix. Each constraint line contains
three tab-separated sparse linear combinations. A combination is a comma list
of `index:decimal_coefficient` pairs; an empty combination is zero. The witness
contains exactly one `index<TAB>canonical_decimal_field_value` line per variable.
Negative coefficients in `(-p,p)` are field representatives, while witnesses
must already be in `[0,p)`. The relation digest is SHA-256 of exact
`constraints.tsv` bytes. The bundle digest is SHA-256 of UTF-8 bytes in this
exact order (the separator shown as `\0` is one NUL byte):

```
header.json\0<header_sha256>\n
constraints.tsv\0<constraints_sha256>\n
assignment.tsv\0<assignment_sha256>\n
```

Distributed import streams both large files through Spark. It collects only
the public prefix, never the complete constraint set or witness. It validates
all variable bounds and values, row count, unique complete witness indices,
constant one, and satisfaction before starting setup. Explicit zero constant
terms keep empty A/B/C sides present in the author's joins. It independently
requires the public prefix to equal `public_k_values` decomposed with
`limb_bits`, and verifies the declared original modulus is exactly `N^2` with
the requested actual bit length. A compiler `.incomplete` marker is refused.
Serial import guards `variables + constraints` with `--serial-max-elements`
(default 2,000,000);
this is an object-count guard, not a memory estimate.
The BN254b scalar field permits radix-2 QAP domains only up to `2^50`; the
adapter refuses `constraints + primary_inputs > 2^50` independently of heap.
Distributed partition count must be a power of two because the pinned
`Utils.fillRDD` does not fill every remainder for arbitrary partition counts.
When running Java with `-ea`, use `-da:algebra.fft.FFTAuxiliary`: that author's
class asserts a long domain against an `int` left shift, which is wrong above
`2^30`. The adapter retains explicit complete input and proof checks.
Author serial QAP/FFT uses `int domainSize`, so serial import separately refuses
`constraints + primary_inputs > 2^30` before its round-up can overflow.

Compatibility adaptations are in adapter classes only. The
author's `LinearCombination.evaluate` ignores an index-zero term's coefficient;
`CorrectLinearCombination` applies every coefficient when serially evaluating
imported relations. The author's `Proof` is not Java-serializable, so the proof
artifact digest hashes the three affine curve-point strings in fixed tagged
order. This digest identifies a run artifact and is not a wire protocol.
Parameter `ONE` classes are initialized before Spark starts, with explicit
`one()/zero()` checks, because upstream curve/parameter mutual static references
can otherwise create points with null instance parameters when Kryo loads
the point class first. This warm-up is outside the measured proof phases.
`RobustSerialSetup` and `RobustDistributedSetup` preserve the complete MIT
author setup source, apart from package/class names and two scalar-width lines
in each class. The original code uses a random point's coordinate bit length
as the fixed-base scalar width; a 253-bit point with an 11-bit window truncates
the top bit of a 254-bit field scalar. Both copied classes use the fixed scalar
modulus bit length (254). `setup_scalar_width.patch` records every difference,
and `setup_source_provenance.json` records the source hashes. The regression
`qa/FixedScalarWidthRegression.java` reproduces this failure separately for G1
and G2 and verifies the correction against direct scalar multiplication.
Source: [pinned author SerialSetup](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/src/main/java/zk_proof_systems/zkSNARK/SerialSetup.java),
[pinned author DistributedSetup](https://github.com/scipr-lab/dizk/blob/29fb0f78964cd6019029d20228ee0bee3184e64d/src/main/java/zk_proof_systems/zkSNARK/DistributedSetup.java).

`--synthetic-smoke` uses the author's fixed small R1CS constructor (64 constraints,
2 primary inputs) solely to exercise the engine. CSV marks it
`scope=synthetic_smoke,same_statement=false,paillier_bits=0,messages=0`; it must
not enter a formal Paillier performance comparison.

`--adapter-self-test --input DIR` accepts only inputs tagged
`scope=arithmetic_self_test,same_statement=false`. It exercises the same import,
setup, prover, and verifier on explicitly identified small arithmetic/compiler
fixtures, including toy Paillier moduli. Default formal mode rejects these
fixtures. Arithmetic self-test rows are excluded from the eight-point formal run.

`input_prepare_seconds` includes exact file hashing and serial import where
applicable. `rdd_materialize_seconds` includes distributed parsing, cache
materialization, count/uniqueness checks, and public-prefix import.
`r1cs_validate_seconds` includes complete relation satisfaction. `setup_seconds`
includes setup and explicit count actions for all four proving-key query RDDs.
`prove_seconds` includes the author's complete QAP/MSM prover and the small
affine proof-digest step. `verify_seconds` measures the author's complete pairing
verifier. `proof_system_seconds` is setup + prove + verify. First-repeat
`total_task_seconds` additionally includes Spark initialization; all repeats
include import/materialization/validation but exclude final CSV append and
cleanup. Each repeat generates a new setup and proof. Failure to parse,
satisfy, or verify exits nonzero and produces no successful row for that repeat.
The formal experiment requests one repeat. Larger `--repeat` values reuse one
Spark context; the upstream code does not explicitly release every intermediate
cache. For independent repeated performance samples, use a fresh JVM with
`--repeat 1` for each sample, preserving every successful or failed run.

`threads=N` in distributed CSV means configured `local[N]` Spark task slots,
not an observation that N workers remain active throughout every phase.
The pairing verifier is serial. Serial CSV records `threads=1,partitions=0`
and retains the CLI value separately as `requested_threads`.

This is a research performance adapter. The pinned author's `Fp.random` draws
`nextLong()` before constructing a scalar, rather than sampling the entire
BN254 scalar field. The adapter supplies fresh OS-backed seed bytes to the
author's existing API and preserves that upstream behavior. Neither this
adapter nor a successful experiment establishes production cryptographic
security or the soundness of a separately generated non-native compiler.
