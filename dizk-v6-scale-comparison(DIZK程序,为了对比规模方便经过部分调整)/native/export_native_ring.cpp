// Read-only adapter: exports an actual v6 CBatchEnc circuit and its witness.
// This is a composite-ring diagnostic, NOT a BN254 R1CS conversion.
#include "app/CBatchEnc.hpp"
#include "app/CEnc.hpp"
#include "app/utils/MemoryUtils.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#ifdef _OPENMP
#include <omp.h>
#endif

namespace {
// Final DIZK curve selection: BN254b. Diagnostic only; the v6 modulus remains N².
const char *BN254B_FR =
  "17855808334804902850260923831770255773646114952324966112694569107431857586177";
typedef chrono::steady_clock Clock;
double elapsed(const Clock::time_point &start) {
  return chrono::duration<double>(Clock::now() - start).count();
}

size_t positiveSize(const string &s, const char *name, bool allowZero = false) {
  if (s.empty() || s[0] == '-') throw invalid_argument(string("invalid ") + name);
  size_t consumed = 0;
  const unsigned long long v = stoull(s, &consumed);
  if (consumed != s.size() || v > numeric_limits<size_t>::max() || (!allowZero && v == 0))
    throw invalid_argument(string("invalid ") + name);
  return static_cast<size_t>(v);
}

binary_t fromHex(const string &s) {
  if (s.size() % 2 != 0) throw invalid_argument("seed-hex must have even length");
  binary_t result;
  for (size_t i = 0; i < s.size(); i += 2) {
    size_t consumed = 0;
    const auto v = stoul(s.substr(i, 2), &consumed, 16);
    if (consumed != 2 || v > 255) throw invalid_argument("invalid seed-hex");
    result.push_back(static_cast<uint8_t>(v));
  }
  if (result.empty()) throw invalid_argument("seed-hex cannot be empty");
  return result;
}

string decimal(const ZZ &value) { ostringstream s; s << value; return s.str(); }
ZZ representative(const ZZ_p &value) { return conv<ZZ>(value); }

ZZ dot(const shared_ptr<Matrix> &wire, const shared_ptr<Matrix> &values, const ZZ &p) {
  if (wire == nullptr || wire->m != 1 || values == nullptr || values->m != 1)
    throw invalid_argument("export must precede circuit grouping/release");
  ZZ sum(0);
  for (const auto &item : wire->values[0]) {
    if (item.first >= values->n) throw invalid_argument("wire exceeds witness bounds");
    sum += representative(item.second) * representative(values->cell(0, item.first));
    sum %= p;
  }
  return sum;
}

void emitMatrix(ofstream &out, size_t row, const char *side,
                const shared_ptr<Matrix> &wire, size_t base, const ZZ &p, size_t gates) {
  if (wire == nullptr || wire->m != 1)
    throw invalid_argument("only ungrouped sparse source matrices can be exported");
  for (const auto &item : wire->values[0]) {
    if (item.first >= gates) throw invalid_argument("wire exceeds gate count");
    const ZZ value = representative(item.second);
    if (value < 0 || value >= p) throw invalid_argument("noncanonical coefficient");
    if (value != 0) out << row << '\t' << side << '\t' << base + item.first << '\t' << value << '\n';
  }
}

string rawWireTerms(const shared_ptr<CBase> &circuit, size_t q) {
  ostringstream out;
  bool first = true;
  const shared_ptr<Matrix> wires[] = {circuit->Wqa[q], circuit->Wqb[q], circuit->Wqc[q]};
  const char *names[] = {"A", "B", "C"};
  for (size_t group = 0; group < 3; group++)
    for (const auto &term : wires[group]->values[0]) {
      const ZZ coefficient = representative(term.second);
      if (coefficient == 0) continue;
      if (!first) out << ',';
      first = false;
      out << names[group] << ':' << term.first << ':' << coefficient;
    }
  return out.str();
}
}

int main(int argc, char **argv) {
  try {
    size_t bits = 512, messages = 1, rangeProofCount = 128, slotBytes = 4, messagesPerBatch = 15, repeat = 1;
    string output, seedHex = "d17e5120a11ce7c0de51206412800415aa";
    bool runV6Proof = false;
    for (int i = 1; i < argc; i++) {
      const string option(argv[i]);
      if (option == "--help") {
        cout << "export_native_ring --output EXISTING_DIR [--bits 512] [--messages 1]\n"
             << "  [--range-proof-count 128] [--slot-bytes 4] [--messages-per-batch 15]\n"
             << "  [--seed-hex EVEN_HEX]\n";
        cout << "  [--run-v6-proof] original commit/prove/verify for this exported instance\n";
        return 0;
      }
      if (option == "--run-v6-proof") { runV6Proof = true; continue; }
      if (i + 1 >= argc) throw invalid_argument("option needs a value: " + option);
      const string value(argv[++i]);
      if (option == "--output") output = value;
      else if (option == "--bits") bits = positiveSize(value, "bits");
      else if (option == "--messages") messages = positiveSize(value, "messages");
      else if (option == "--range-proof-count") rangeProofCount = positiveSize(value, "range-proof-count");
      else if (option == "--slot-bytes") slotBytes = positiveSize(value, "slot-bytes");
      else if (option == "--messages-per-batch") messagesPerBatch = positiveSize(value, "messages-per-batch");
      else if (option == "--seed-hex") seedHex = value;
      else if (option == "--repeat") repeat = positiveSize(value, "repeat");
      else throw invalid_argument("unknown option: " + option);
    }
    if (output.empty()) throw invalid_argument("--output is required and must already exist");
    if (bits < 16 || bits % 8 != 0) throw invalid_argument("bits must be a multiple of 8 and at least 16");
    if (slotBytes > bits / 8 || (bits / 8) % slotBytes != 0)
      throw invalid_argument("slot-bytes must divide the public-key byte length");
    const auto baseSeed = fromHex(seedHex);

    // Each retry gets a different seed. A nominal 64-byte request can yield 511 bits.
    shared_ptr<PaillierEncryption> crypto;
    size_t keyAttempt = 0;
    const auto keyStart = Clock::now();
    for (; keyAttempt < 1024; keyAttempt++) {
      auto seed = baseSeed;
      for (size_t shift = 0; shift < 4; shift++)
        seed.push_back(static_cast<uint8_t>((keyAttempt >> (8 * shift)) & 255));
      crypto = make_shared<PaillierEncryption>(bits / 8, seed);
      if (NumBits(crypto->getPublicKey()) == static_cast<long>(bits)) break;
    }
    if (keyAttempt == 1024) throw runtime_error("could not generate the requested actual modulus bits");
    const double keySeconds = elapsed(keyStart);
    const ZZ n = crypto->getPublicKey(), p = crypto->getGroupP();
    if (p != n * n) throw invalid_argument("v6 arithmetic modulus differs from N squared");
    ZZ_p::init(p);
    auto circuit = make_shared<CBatchEnc>(crypto, messages, rangeProofCount, slotBytes, messagesPerBatch);

    // Reproduces EndToEnd.cpp63-79 structured messages; no complete proof or generators.
    Vec<ZZ> payload;
    for (size_t i = 0; i < messages; i++) {
      const ZZ max = conv<ZZ>(2) << (circuit->slotsPerMsg - 1);
      const ZZ remainder = conv<ZZ>(i) % max;
      const string binary = ConvertUtils::toBinaryString(remainder);
      ZZ m(0);
      for (size_t j = 0; j < binary.size(); j++) {
        m <<= (slotBytes * 8);
        m += binary[j] - '0';
      }
      payload.append(m);
    }
    auto start = Clock::now();
    circuit->encrypt(payload);
    const double encryptSeconds = elapsed(start);
    start = Clock::now();
    const auto challenge = circuit->calculateLjir();
    const auto response = circuit->calculateLj(challenge);
    circuit->wireUp(challenge, response);
    const double proverWireSeconds = elapsed(start);
    start = Clock::now();
    circuit->run(challenge, response);
    const double assignSeconds = elapsed(start);
    ZZ_p::init(p);

    const size_t gates = circuit->gateCount, linear = circuit->linearCount;
    auto singleCircuit = make_shared<CEnc>(crypto);
    singleCircuit->wireUp();
    const size_t singleGates = singleCircuit->gateCount, singleLinear = singleCircuit->linearCount;
    singleCircuit->releaseMemory();
    if (circuit->Wqa.size() != linear || circuit->Wqb.size() != linear ||
        circuit->Wqc.size() != linear || circuit->Kq.length() != static_cast<long>(linear))
      throw invalid_argument("constraint vector sizes disagree");
    for (size_t i = 0; i < gates; i++) {
      const ZZ a = representative(circuit->A->cell(0, i));
      const ZZ b = representative(circuit->B->cell(0, i));
      const ZZ c = representative(circuit->C->cell(0, i));
      if ((a * b) % p != c) throw invalid_argument("invalid multiplication gate " + to_string(i));
    }
    for (size_t q = 0; q < linear; q++) {
      const ZZ value = (dot(circuit->Wqa[q], circuit->A, p) +
                        dot(circuit->Wqb[q], circuit->B, p) +
                        dot(circuit->Wqc[q], circuit->C, p)) % p;
      if (value != representative(circuit->Kq[q]))
        throw invalid_argument("invalid linear constraint " + to_string(q));
    }

    // Raw source-ring schema for a separate sound non-native arithmetic compiler.
    ofstream rawGates(output + "/gates.tsv"), rawLinear(output + "/linear.tsv");
    if (!rawGates || !rawLinear) throw runtime_error("cannot create raw circuit files");
    for (size_t i = 0; i < gates; i++)
      rawGates << i << '\t' << representative(circuit->A->cell(0, i)) << '\t'
               << representative(circuit->B->cell(0, i)) << '\t'
               << representative(circuit->C->cell(0, i)) << '\n';
    for (size_t q = 0; q < linear; q++) {
      rawLinear << q << '\t' << representative(circuit->Kq[q]) << '\t' << rawWireTerms(circuit, q) << '\n';
    }
    rawGates.close(); rawLinear.close();
    if (!rawGates || !rawLinear) throw runtime_error("raw circuit write failed");

    uint64_t fingerprint = UINT64_C(14695981039346656037);
    long plaintextBits = 0;
    for (size_t i = 0; i < messages; i++) {
      plaintextBits = max(plaintextBits, NumBits(payload[i]));
      for (unsigned char byte : decimal(payload[i])) {
        fingerprint ^= static_cast<uint64_t>(byte);
        fingerprint *= UINT64_C(1099511628211);
      }
      fingerprint ^= UINT64_C(10);
      fingerprint *= UINT64_C(1099511628211);
    }
    ostringstream fingerprintHex;
    fingerprintHex << hex << fingerprint;

    json header = json::object();
    header["format"] = "v6-ring-v1";
    header["modulus"] = decimal(p);
    header["paillier_modulus"] = decimal(n);
    header["scalar_domain_kind"] = "composite_ring_mod_n_squared";
    header["gate_count"] = gates;
    header["linear_count"] = linear;
    header["native_total_constraints"] = gates + linear;
    header["single_encryption_gate_count"] = singleGates;
    header["single_encryption_linear_count"] = singleLinear;
    header["bits"] = NumBits(n);
    header["modulus_bits"] = NumBits(p);
    header["messages"] = messages;
    header["range_proof_count"] = rangeProofCount;
    header["slot_bytes"] = slotBytes;
    header["slots_per_message"] = circuit->slotsPerMsg;
    header["messages_per_batch"] = messagesPerBatch;
    header["batch_count"] = circuit->batchCount;
    header["ciphertext_count"] = messages + rangeProofCount + circuit->batchCount;
    header["workload"] = "v6_full_cbatchenc";
    header["export_scope"] = "complete_CBatchEnc_arithmetic_relation_including_normal_range_and_packed_messages";
    header["comparison_scope"] = "same_cbatchenc_arithmetic_relation_requires_sound_non_native_translation";
    header["witness_visibility"] = "A,B,C are private; Kq is public; public N and all wire coefficients are circuit parameters";
    header["gates_tsv"] = "continuous gate_index<TAB>A_decimal<TAB>B_decimal<TAB>C_decimal; every gate exported";
    header["linear_tsv"] = "continuous row_index<TAB>public_K_decimal<TAB>comma-separated A|B|C:gate_index:canonical_coefficient";
    header["native_values"] = "canonical integers 0<=value<N_squared; exact integer modular relation, never reduced to BN254";
    header["native_assignment_checked"] = true;
    header["payload_fingerprint"] = fingerprintHex.str();
    header["payload_fingerprint_algorithm"] = "FNV1a64(decimal ASCII of each normal plaintext followed by LF), lowercase unpadded hex";
    header["plaintext_bits"] = plaintextBits;
    header["public_group_Q"] = decimal(crypto->getGroupQ());
    header["public_group_G"] = decimal(representative(crypto->getGroupG()));
    header["ciphertexts"] = json::object();
    const Vec<ZZ_p> *cipherVecs[] = {&circuit->Cm, &circuit->CRj, &circuit->Cm_};
    const char *cipherNames[] = {"normal", "range_masks", "packed"};
    for (size_t group = 0; group < 3; group++) {
      header["ciphertexts"][cipherNames[group]] = json::array();
      for (long i = 0; i < cipherVecs[group]->length(); i++)
        header["ciphertexts"][cipherNames[group]].push_back(decimal(representative((*cipherVecs[group])[i])));
    }
    header["challenge_binding"] = "original calculateLjir(ciphertexts) NTL PRG preprocessing; original wire coefficients exported in linear.tsv";
    header["statement_instance_status"] = "new_deterministic_instance_not_the_archived_timing_instance";
    header["seed_hex"] = seedHex;
    header["key_generation_attempt"] = keyAttempt;
    header["no_full_proof_run"] = !runV6Proof;
    header["exporter_version"] = "native-ring-audit-v1";
    ofstream rawHeader(output + "/header.json");
    if (!rawHeader) throw runtime_error("cannot create header.json");
    rawHeader << header.dump(2) << '\n';
    rawHeader.close();
    if (!rawHeader) throw runtime_error("header write failed");

    const size_t aBase = 1 + linear, bBase = aBase + gates, cBase = bBase + gates;
    ofstream assignment(output + "/assignment.tsv"), matrix(output + "/matrix.tsv");
    if (!assignment || !matrix) throw runtime_error("cannot create export files");
    assignment << "0\t1\n";
    for (size_t q = 0; q < linear; q++)
      assignment << q + 1 << '\t' << representative(circuit->Kq[q]) << '\n';
    const shared_ptr<Matrix> witness[] = {circuit->A, circuit->B, circuit->C};
    const size_t bases[] = {aBase, bBase, cBase};
    for (size_t group = 0; group < 3; group++)
      for (size_t i = 0; i < gates; i++)
        assignment << bases[group] + i << '\t' << representative(witness[group]->cell(0, i)) << '\n';
    for (size_t i = 0; i < gates; i++) {
      matrix << i << "\tA\t" << aBase + i << "\t1\n"
             << i << "\tB\t" << bBase + i << "\t1\n"
             << i << "\tC\t" << cBase + i << "\t1\n";
    }
    for (size_t q = 0; q < linear; q++) {
      const size_t row = gates + q;
      emitMatrix(matrix, row, "A", circuit->Wqa[q], aBase, p, gates);
      emitMatrix(matrix, row, "A", circuit->Wqb[q], bBase, p, gates);
      emitMatrix(matrix, row, "A", circuit->Wqc[q], cBase, p, gates);
      matrix << row << "\tA\t" << q + 1 << '\t' << p - 1 << '\n'
             << row << "\tB\t0\t1\n";
    }
    assignment.close(); matrix.close();
    if (!assignment || !matrix) throw runtime_error("export write failed");
    json meta = json::object();
    meta["format"] = "v6-native-ring-v1";
    meta["scalar_domain_kind"] = "composite_ring_mod_n_squared";
    meta["ring_modulus"] = decimal(p);
    meta["paillier_public_key"] = decimal(n);
    meta["ring_modulus_bits"] = NumBits(p);
    meta["bn254_diagnostic_curve"] = "BN254b";
    meta["bn254_field_modulus"] = BN254B_FR;
    meta["bn254_modulus_role"] = "diagnostic_target_scalar_field_only_not_the_native_ring_modulus";
    meta["bn254_direct_import_allowed"] = false;
    meta["non_native_translation_status"] = "not_in_this_native_exporter_use_separate_compiler";
    meta["paillier_bits"] = NumBits(n);
    meta["messages"] = messages;
    meta["range_proof_count"] = rangeProofCount;
    meta["slot_bytes"] = slotBytes;
    meta["slots_per_message"] = circuit->slotsPerMsg;
    meta["messages_per_batch"] = messagesPerBatch;
    meta["batch_count"] = circuit->batchCount;
    meta["ciphertexts"] = messages + rangeProofCount + circuit->batchCount;
    meta["num_multiplication_constraints"] = gates;
    meta["num_linear_constraints"] = linear;
    meta["num_constraints"] = gates + linear;
    meta["num_variables"] = 1 + linear + 3 * gates;
    meta["num_public_inputs"] = 1 + linear;
    meta["constant_variable_index"] = 0;
    meta["private_assignment_offsets"] = {{"A", aBase}, {"B", bBase}, {"C", cBase}};
    meta["public_input_interpretation"] = "index0=1; index1..Q=Kq (including fixed/repeated N,0,1 and ciphertext/Lj values)";
    meta["statement_label"] = "v6 CBatchEnc multiplication and linear constraints modulo the public N squared";
    meta["statement_instance_status"] = "new_deterministic_instance_not_the_archived_timing_instance";
    meta["challenge_binding"] = "Ljir regenerated by original calculateLjir; NTL PRG derivation remains verifier preprocessing, not arithmetic constraints";
    meta["witness_generation"] = "original CBatchEnc encrypt/wireUp/run before any optional native proof";
    meta["run_v6_proof_requested"] = runV6Proof;
    meta["native_ring_assignment_checked"] = true;
    meta["seed_hex"] = seedHex;
    meta["key_generation_attempt"] = keyAttempt;
    meta["exporter_version"] = "native-ring-audit-v1";
    ofstream metadata(output + "/meta.json");
    if (!metadata) throw runtime_error("cannot create meta.json");
    metadata << meta.dump(2) << '\n';
    metadata.close();
    if (!metadata) throw runtime_error("metadata write failed");
    cout << "NATIVE_RING_EXPORTED actual_bits=" << NumBits(n) << " messages=" << messages
         << " gates=" << gates << " linear=" << linear << " bn254_compatible=false\n";
    if (runV6Proof) {
      // Same actual circuit/witness that was exported above. Export/audit I/O is excluded.
      const auto normalCipher = circuit->Cm;
      const auto packedCipher = circuit->Cm_;
      const auto rangeCipher = circuit->CRj;
      const auto groupQ = crypto->getGroupQ();
      auto groupP = crypto->getGroupP();
      auto groupG = crypto->getGroupG();
      start = Clock::now();
      const auto generatorCount = circuit->estimateGeneratorsRequired();
      const auto generators = crypto->genGenerators(generatorCount);
      const double generatorsSeconds = elapsed(start);
      start = Clock::now();
      auto prover = circuit->generateProverAndRelease(generators);
      circuit->releaseMemory();
      circuit.reset();
      const double proverPrepareSeconds = elapsed(start);
      trimProcessMemory();

      Vec<ZZ_p> commits, polynomialCommits, proof;
      start = Clock::now();
      prover->commit(commits);
      const double commitSeconds = elapsed(start);
      start = Clock::now();
      prover->zkp->setCommits(commits);
      const auto y1 = prover->zkp->calculateY();
      const double proverYSeconds = elapsed(start);
      start = Clock::now();
      prover->polyCommit(y1, polynomialCommits);
      const double polyCommitSeconds = elapsed(start);
      trimProcessMemory();
      start = Clock::now();
      prover->zkp->setPolyCommits(polynomialCommits);
      const auto x1 = prover->zkp->calculateX();
      const double proverXSeconds = elapsed(start);
      start = Clock::now();
      prover->prove(y1, x1, proof);
      const double proveCoreSeconds = elapsed(start);
      const double provingSeconds = proverYSeconds + polyCommitSeconds + proverXSeconds + proveCoreSeconds;
      prover->releaseMemory(); prover.reset();
      trimProcessMemory();

      start = Clock::now();
      auto publicCrypto = make_shared<PaillierEncryption>(n, groupQ, groupP, groupG);
      auto verifierCircuit = make_shared<CBatchEnc>(publicCrypto, messages, rangeProofCount, slotBytes, messagesPerBatch);
      verifierCircuit->setCipher(normalCipher, packedCipher, rangeCipher);
      const auto verifierChallenge = verifierCircuit->calculateLjir();
      if (verifierChallenge != challenge)
        throw invalid_argument("verifier ciphertext-derived challenge differs from exported prover challenge");
      verifierCircuit->wireUp(verifierChallenge, response);
      if (verifierCircuit->gateCount != gates || verifierCircuit->linearCount != linear)
        throw invalid_argument("verifier circuit dimensions differ from exported source relation");
      // Independently match all public W/Kq to the serialized statement before releasing wires.
      ifstream publicRows(output + "/linear.tsv");
      if (!publicRows) throw runtime_error("cannot reopen exported public statement");
      string rowText;
      size_t matchedRows = 0;
      while (getline(publicRows, rowText)) {
        if (!rowText.empty() && rowText.back() == '\r') rowText.pop_back();
        const auto firstTab = rowText.find('\t');
        const auto secondTab = rowText.find('\t', firstTab + 1);
        if (firstTab == string::npos || secondTab == string::npos || matchedRows >= linear)
          throw invalid_argument("invalid exported public row during native proof binding");
        const size_t index = positiveSize(rowText.substr(0, firstTab), "linear row", true);
        ZZ publicK;
        istringstream valueStream(rowText.substr(firstTab + 1, secondTab - firstTab - 1));
        valueStream >> publicK;
        if (index != matchedRows || !valueStream || publicK != representative(verifierCircuit->Kq[index]))
          throw invalid_argument("native verifier public K differs from exported source statement");
        if (rowText.substr(secondTab + 1) != rawWireTerms(verifierCircuit, index))
          throw invalid_argument("native verifier public W differs from exported source statement");
        matchedRows++;
      }
      if (matchedRows != linear) throw invalid_argument("incomplete exported public statement");
      auto verifier = verifierCircuit->generateVerifierAndRelease(generators);
      verifierCircuit->releaseMemory(); verifierCircuit.reset();
      const double verifierPrepareSeconds = elapsed(start);
      trimProcessMemory();
      start = Clock::now();
      verifier->setCommits(commits);
      const auto y2 = verifier->calculateY();
      verifier->setPolyCommits(polynomialCommits);
      const auto x2 = verifier->calculateX();
      const bool verified = verifier->verify(proof, y2, x2);
      const double verifySeconds = elapsed(start);
      const size_t matrixRows = verifier->m, matrixColumns = verifier->n;
      verifier->releaseMemory(); verifier.reset();

      json timing = json::object();
      timing["format"] = "v6-same-statement-timing-v1";
      timing["scheme"] = "v6_original";
      timing["messages"] = messages;
      timing["repeat"] = repeat;
#ifdef _OPENMP
      timing["openmp_enabled"] = true;
      timing["openmp_version_macro"] = _OPENMP;
      timing["openmp_max_threads"] = omp_get_max_threads();
#else
      timing["openmp_enabled"] = false;
      timing["openmp_max_threads"] = 1;
#endif
      const char *requestedThreads = std::getenv("OMP_NUM_THREADS");
      timing["omp_num_threads_environment"] = requestedThreads ? string(requestedThreads) : string("unset");
      timing["actual_bits"] = NumBits(n);
      timing["paillier_modulus"] = decimal(n);
      timing["ring_modulus"] = decimal(p);
      timing["gate_count"] = gates;
      timing["linear_count"] = linear;
      timing["payload_fingerprint"] = fingerprintHex.str();
      timing["plaintext_bits"] = plaintextBits;
      timing["verified"] = verified;
      timing["raw_public_statement_matched"] = true;
      timing["raw_public_WKq_matched"] = true;
      timing["raw_export_same_circuit_and_witness"] = true;
      timing["workload"] = "v6_full_cbatchenc";
      timing["key_generation_seconds"] = keySeconds;
      timing["all_message_encryption_seconds"] = encryptSeconds;
      timing["prover_challenge_and_wire_seconds"] = proverWireSeconds;
      timing["value_assignment_seconds"] = assignSeconds;
      timing["generator_setup_seconds"] = generatorsSeconds;
      timing["prover_prepare_seconds"] = proverPrepareSeconds;
      timing["commit_seconds"] = commitSeconds;
      timing["prover_Y_seconds"] = proverYSeconds;
      timing["polynomial_commit_seconds"] = polyCommitSeconds;
      timing["prover_X_seconds"] = proverXSeconds;
      timing["prove_core_seconds"] = proveCoreSeconds;
      timing["proving_seconds"] = provingSeconds;
      timing["proof_generation_seconds"] = commitSeconds + provingSeconds;
      timing["verifier_prepare_seconds"] = verifierPrepareSeconds;
      timing["verify_seconds"] = verifySeconds;
      timing["proof_pipeline_sum_seconds"] = encryptSeconds + proverWireSeconds + assignSeconds + generatorsSeconds +
          proverPrepareSeconds + commitSeconds + provingSeconds + verifierPrepareSeconds + verifySeconds;
      timing["matrix_rows"] = matrixRows;
      timing["matrix_columns"] = matrixColumns;
      timing["generator_count"] = generatorCount;
      timing["serialized_group_element_count"] = commits.length() + polynomialCommits.length() + proof.length();
      timing["timing_scope"] = "same_statement_original_commit_plus_challenges_polyCommit_prove_and_original_verify";
      timing["timing_notes"] = {
        "Proof generation is commit_seconds + proving_seconds; commit must be included when compared with complete DIZK prover.",
        "Proving seconds sums Y, polyCommit, X and prove calls. Verify seconds includes verifier Y/X derivation and verify.",
        "Export/audit file I/O and raw statement binding are excluded from proof generation and verify.",
        "verifier_prepare_seconds includes public-key import, ciphertext challenge/wire, all raw W/K binding file read/comparison, conversion and release.",
        "Pipeline is a sum of measured preparation/proof phases, not a contiguous wall-clock interval; no circuitTime division by two.",
        "Generator setup is measured separately; original generator generation occurs after export and does not alter the exported witness.",
        "Post-proof homomorphic aggregation/decomposition from EndToEnd is not performed.",
        "This fresh baseline belongs to the exported N/Kq/W/ABC instance; historical timing CSV is not used."
      };
      ofstream timingFile(output + "/v6_native_timing.json");
      if (!timingFile) throw runtime_error("cannot create native baseline timing");
      timingFile << timing.dump(2) << '\n';
      timingFile.close();
      if (!timingFile) throw runtime_error("native baseline write failed");
      cout << "V6_SAME_STATEMENT_PROOF verified=" << (verified ? "true" : "false")
           << " proof_generation_seconds=" << setprecision(17) << commitSeconds + provingSeconds
           << " verify_seconds=" << verifySeconds << '\n';
      if (!verified) throw invalid_argument("same-statement original v6 proof did not verify");
    } else {
      circuit->releaseMemory();
    }
    return 0;
  } catch (const exception &e) {
    cerr << "NATIVE_RING_EXPORT_ERROR: " << e.what() << '\n';
    return 2;
  }
}
