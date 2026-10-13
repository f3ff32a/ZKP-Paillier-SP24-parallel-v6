# Independent arithmetic review

Reviewed the current `work/dizk-compiler/compile_ring_inputs.py` against the
native relation `A_i B_i = C_i (mod M)` and `sum c_j X_j = K (mod M)`, where
`M=N^2`. This is a source-level mathematical review, not a cryptographic
security audit or evidence that the complete eight-point experiment ran.

The central soundness argument holds for the implemented construction:

* Every integer limb and unsigned shifted carry is reconstructed from Boolean
  bits. In a prime field, `b(b-1)=0` restricts each bit to zero or one.
  Reconstruction equalities use an absolute integer bound below `Fr`.
* Each private limb product is constrained by multiplication, with both input
  limbs range-constrained and their maximum product below `Fr`. Its field
  value therefore uniquely equals the ordinary integer product. It needs no
  separate product range constraint.
* A column equation includes the previous carry, the next carry multiplied
  by the radix, and the appropriate offset constants. The initial and final
  carries are zero. The compiler checks an absolute bound below `Fr` using
  the full permitted ranges, including all unsigned carry bits. Thus a zero
  field equality is a zero integer equality for every permitted witness.
  Multiplying columns by radix powers and summing cancels all internal carries.
* A multiplication gate consequently enforces `AB-C=qM`. The nonnegative
  `k+1`-bit quotient suffices for canonical ring inputs when `k=bit_length(M)`.
  Every legitimate original ring assignment has such a canonical representative.
* A linear row uses the signed representative of every public coefficient.
  The private shifted quotient is `q+2 sum negative_coefficient_magnitudes+2`.
  For permitted `k`-bit wire representatives, `X/M<2`, so the shift keeps this
  quotient nonnegative, and the chosen `bit_length(2 sum |c|+4)` covers it.
  Column telescoping then enforces the original linear congruence exactly.

ABC wire integers are bounded by `2^k-1`, rather than by `M-1`. This permits
some additional integer representatives of the same ring element. It does not
enlarge the set of existential modular statements: reducing all wires modulo
M preserves every multiplication and linear relation; conversely every valid
original assignment has canonical representatives accepted by the lifting.
It should not be described as proving unique canonical integer witnesses.

Two integrity improvements were requested during review. The Java adapter now
checks the public prefix against the header's `public_k_values` and limb width,
checks `M=N^2`, and refuses `.incomplete`. The compiler must snapshot all three
source-file hashes before its first `inspect` read and confirm them at completion,
so a long compile cannot mix an earlier public statement with later source bytes.

Review boundaries: the original v6 exporter must still establish that its native
gate/linear relation contains the complete intended computation. This review
does not turn an arithmetic self-test into a complete Paillier computation,
and does not establish production security of the author's proof implementation.
