package comparison;

import algebra.curves.barreto_naehrig.bn254b.BN254bFields.BN254bFr;
import org.apache.spark.api.java.JavaPairRDD;
import org.apache.spark.api.java.JavaRDD;
import org.apache.spark.api.java.JavaSparkContext;
import org.apache.spark.storage.StorageLevel;
import relations.objects.Assignment;
import relations.objects.LinearCombination;
import relations.objects.LinearTerm;
import relations.objects.R1CSConstraint;
import relations.objects.R1CSConstraints;
import relations.objects.R1CSConstraintsRDD;
import relations.r1cs.R1CSRelation;
import relations.r1cs.R1CSRelationRDD;
import scala.Tuple2;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.Serializable;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/** Import complete BN254b R1CS: strict ring lifting or explicitly selected constraint scale. */
final class R1CSIO {
    static final BigInteger MODULUS = new BigInteger(
            "17855808334804902850260923831770255773646114952324966112694569107431857586177");
    static final StorageLevel STORAGE = StorageLevel.MEMORY_AND_DISK_SER();

    static class Header {
        Path directory;
        long variables, constraints;
        int primaryInputs, messages, paillierBits;
        int referencePaillierBits;
        long multiplicationConstraints, linearConstraints;
        String generatorId = "not_applicable", referenceCsvHash;
        BN254bFr[] expectedPublic;
        String statementId, relationHash, headerHash, constraintHash, assignmentHash, bundleHash;
    }
    static final class ScaleHeader extends Header {}

    static Map<String, Object> preflight(Path directory) throws Exception {
        if (Files.exists(directory.resolve(".incomplete"))) throw new IllegalArgumentException("INCOMPLETE_INPUT: compiler has not completed this bundle");
        Path file = directory.resolve("header.json");
        if (!Files.exists(file) && Files.exists(directory.resolve("meta.json"))) {
            Map<String, Object> nativeHeader = Json.object(new String(Files.readAllBytes(directory.resolve("meta.json")), StandardCharsets.UTF_8));
            if (String.valueOf(nativeHeader.get("format")).startsWith("v6-native-ring")) {
                throw new IllegalArgumentException("NON_NATIVE_TRANSLATION_REQUIRED: v6 constraints are over composite Z_(N^2), not BN254bFr; no automatic modular reduction is permitted");
            }
        }
        if (Files.size(file) > 2 * 1024 * 1024) throw new IllegalArgumentException("Header exceeds 2 MiB");
        Map<String, Object> json = Json.object(new String(Files.readAllBytes(file), StandardCharsets.UTF_8));
        if (!"dizk-r1cs-v2".equals(string(json, "format"))) throw new IllegalArgumentException("Unsupported R1CS format");
        BigInteger field = new BigInteger(string(json, "field_modulus"));
        if (!field.equals(MODULUS)) throw new IllegalArgumentException("R1CS_DOMAIN_MISMATCH: expected BN254bFr modulus " + MODULUS + "; got " + field + ". Non-native translation is required.");
        return json;
    }

    static Header header(Path directory, boolean arithmeticSelfTest) throws Exception {
        return header(directory, arithmeticSelfTest, false);
    }

    static Header header(Path directory, boolean arithmeticSelfTest, boolean constraintScale) throws Exception {
        if (arithmeticSelfTest && constraintScale) throw new IllegalArgumentException("Self-test and constraint-scale modes are mutually exclusive");
        Path file = directory.resolve("header.json");
        Map<String, Object> json = preflight(directory);
        Header header = constraintScale ? new ScaleHeader() : new Header();
        header.directory = directory;
        header.variables = integer(json, "variables");
        header.constraints = integer(json, "constraints");
        long primary = integer(json, "primary_inputs");
        if (header.variables < 2 || header.constraints < 1 || primary < 1 || primary > Integer.MAX_VALUE || primary >= header.variables) {
            throw new IllegalArgumentException("Invalid R1CS counts: counts include constant variable zero, and at least one private variable is required");
        }
        // BN254bFr has two-adicity 50. Its author's radix-2 QAP domain cannot
        // exceed 2^50, independent of how much heap a VM has available.
        if (primary > (1L << 50) || header.constraints > (1L << 50) - primary) {
            throw new IllegalArgumentException("BN254bFr FFT_DOMAIN_LIMIT: constraints + primary_inputs must not exceed 2^50");
        }
        header.primaryInputs = (int) primary;
        header.messages = Math.toIntExact(integer(json, "messages"));
        header.paillierBits = Math.toIntExact(integer(json, "paillier_bits"));
        if (constraintScale) {
            readScaleMetadata(json, header);
        } else if (arithmeticSelfTest) {
            if (!Boolean.FALSE.equals(json.get("same_statement")) || !"arithmetic_self_test".equals(string(json, "scope"))) {
                throw new IllegalArgumentException("--adapter-self-test accepts only scope=arithmetic_self_test,same_statement=false; this is not a formal v6 comparison");
            }
            if (header.paillierBits < 8 || header.paillierBits > 512 || header.messages < 1) throw new IllegalArgumentException("Invalid arithmetic self-test modulus/message metadata");
        } else {
            if (header.paillierBits != 512 || !Arrays.asList(1, 10, 20, 40, 80, 100, 200, 400).contains(header.messages)) {
                throw new IllegalArgumentException("Formal input must use requested actual 512-bit Paillier and an explicit MC from the eight-point experiment");
            }
            if (!Boolean.TRUE.equals(json.get("same_statement")) || !"v6_ring_lifted".equals(string(json, "scope"))) {
                throw new IllegalArgumentException("Input must explicitly identify the strict v6_ring_lifted statement, not a self-test or synthetic circuit");
            }
        }
        // Scale benchmarks are different prime-field statements. Only strict
        // and self-test inputs bind original N, N^2, and public K limbs.
        if (!constraintScale) {
            BigInteger ring = new BigInteger(string(json, "original_ring_modulus"));
            if (ring.signum() <= 0 || ring.bitLength() < header.paillierBits * 2 - 1 || ring.bitLength() > header.paillierBits * 2) throw new IllegalArgumentException("Original Paillier N^2 ring bit-length metadata is inconsistent");
            BigInteger paillier = metadataInteger(json, "paillier_modulus");
            if (paillier.bitLength() != header.paillierBits || !paillier.multiply(paillier).equals(ring)) throw new IllegalArgumentException("Original ring must be exactly the declared Paillier N squared");
            long widthLong = integer(json, "limb_bits");
            if (widthLong < 8 || widthLong > 64) throw new IllegalArgumentException("limb_bits outside compiler range 8..64");
            int width = (int) widthLong;
            int limbs = (ring.bitLength() + width - 1) / width;
            Object publicMetadata = json.get("public_k_values");
            if (!(publicMetadata instanceof List)) throw new IllegalArgumentException("Missing public_k_values limb binding");
            List<?> publicKs = (List<?>) publicMetadata;
            if (1L + (long) publicKs.size() * limbs != header.primaryInputs) throw new IllegalArgumentException("primary_inputs differs from declared original public K limbs");
            header.expectedPublic = new BN254bFr[header.primaryInputs];
            header.expectedPublic[0] = BN254bFr.ONE;
            BigInteger mask = BigInteger.ONE.shiftLeft(width).subtract(BigInteger.ONE);
            int nextPublic = 1;
            for (Object rawK : publicKs) {
                if (!(rawK instanceof String) || !((String) rawK).matches("[0-9]+")) throw new IllegalArgumentException("public_k_values requires decimal strings");
                BigInteger value = new BigInteger((String) rawK);
                if (value.signum() < 0 || value.compareTo(ring) >= 0) throw new IllegalArgumentException("Original public K outside ring");
                for (int limb = 0; limb < limbs; limb++) header.expectedPublic[nextPublic++] = new BN254bFr(value.shiftRight(limb * width).and(mask));
            }
        }
        header.statementId = string(json, "statement_id");
        if (constraintScale && !header.statementId.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("Scale statement_id must be a lowercase SHA256 digest");
        header.relationHash = string(json, "relation_hash");
        header.headerHash = hash(file);
        header.constraintHash = hash(directory.resolve("constraints.tsv"));
        header.assignmentHash = hash(directory.resolve("assignment.tsv"));
        if (!header.relationHash.equals(header.constraintHash)) throw new IllegalArgumentException("relation_hash differs from exact constraints.tsv SHA256");
        if (constraintScale && !json.containsKey("assignment_hash")) throw new IllegalArgumentException("Scale input requires assignment_hash");
        if (json.containsKey("assignment_hash") && !string(json, "assignment_hash").equals(header.assignmentHash)) throw new IllegalArgumentException("assignment_hash mismatch");
        MessageDigest bundle = MessageDigest.getInstance("SHA-256");
        for (String[] entry : new String[][]{{"header.json", header.headerHash}, {"constraints.tsv", header.constraintHash}, {"assignment.tsv", header.assignmentHash}}) {
            bundle.update((entry[0] + "\0" + entry[1] + "\n").getBytes(StandardCharsets.UTF_8));
        }
        header.bundleHash = hex(bundle.digest());
        return header;
    }

    private static void readScaleMetadata(Map<String, Object> json, Header header) {
        if (!Boolean.FALSE.equals(json.get("same_statement")) || !"constraint_scale_benchmark".equals(string(json, "scope"))) {
            throw new IllegalArgumentException("--constraint-scale accepts only scope=constraint_scale_benchmark,same_statement=false");
        }
        header.referencePaillierBits = Math.toIntExact(integer(json, "reference_paillier_bits"));
        if (header.paillierBits != 0 || header.referencePaillierBits != 512
                || !Arrays.asList(1, 10, 20, 40, 80, 100, 200, 400).contains(header.messages)) {
            throw new IllegalArgumentException("Scale input requires paillier_bits=0,reference_paillier_bits=512 and an explicit MC from the eight-point experiment");
        }
        header.multiplicationConstraints = integer(json, "multiplication_constraints");
        header.linearConstraints = integer(json, "linear_constraints");
        if (header.multiplicationConstraints < 1 || header.linearConstraints < 0
                || Math.addExact(header.multiplicationConstraints, header.linearConstraints) != header.constraints
                || header.primaryInputs != 2 || Math.addExact(header.constraints, 3) != header.variables) {
            throw new IllegalArgumentException("Scale counts must satisfy constraints=N+Q,variables=M+3,primary_inputs=2 with N>0,Q>=0");
        }
        header.generatorId = string(json, "generator_id");
        if (!"field_chain_mix_v1".equals(header.generatorId)) throw new IllegalArgumentException("Unsupported scale generator_id");
        header.referenceCsvHash = string(json, "reference_csv_sha256");
        if (!header.referenceCsvHash.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("reference_csv_sha256 must be a lowercase SHA256 digest");
        Object metadata = json.get("public_values");
        if (!(metadata instanceof List) || ((List<?>) metadata).size() != 2) throw new IllegalArgumentException("Scale public_values must contain exactly constant one and final output");
        header.expectedPublic = new BN254bFr[2];
        List<?> publicValues = (List<?>) metadata;
        for (int i = 0; i < 2; i++) {
            if (!(publicValues.get(i) instanceof String)) throw new IllegalArgumentException("Scale public_values requires decimal strings");
            header.expectedPublic[i] = new BN254bFr(decimal((String) publicValues.get(i), false));
        }
        if (!"1".equals(publicValues.get(0))) throw new IllegalArgumentException("Scale public_values[0] must be exactly constant one");
    }

    private static String string(Map<String, Object> json, String key) {
        Object value = json.get(key);
        if (!(value instanceof String) || ((String) value).isEmpty()) throw new IllegalArgumentException("Expected nonempty JSON string: " + key);
        return (String) value;
    }
    private static long integer(Map<String, Object> json, String key) {
        Object value = json.get(key);
        if (!(value instanceof BigDecimal)) throw new IllegalArgumentException("Expected JSON integer: " + key);
        return ((BigDecimal) value).longValueExact();
    }
    private static BigInteger metadataInteger(Map<String, Object> json, String key) {
        Object value = json.get(key);
        if (value instanceof String && ((String) value).matches("[0-9]+")) return new BigInteger((String) value);
        if (value instanceof BigDecimal) return ((BigDecimal) value).toBigIntegerExact();
        throw new IllegalArgumentException("Expected decimal metadata integer: " + key);
    }
    private static void validatePublic(BN254bFr[] values, Header header) {
        if (values.length != header.expectedPublic.length) throw new IllegalArgumentException("Public prefix length mismatch");
        for (int index = 0; index < values.length; index++) {
            if (!header.expectedPublic[index].equals(values[index])) throw new IllegalArgumentException("PUBLIC_INPUT_MISMATCH: witness public prefix differs from declared public value " + index);
        }
    }
    static String hash(Path file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream stream = Files.newInputStream(file)) {
            byte[] buffer = new byte[1024 * 1024];
            int length;
            while ((length = stream.read(buffer)) != -1) digest.update(buffer, 0, length);
        }
        return hex(digest.digest());
    }
    static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) result.append(String.format("%02x", value & 255));
        return result.toString();
    }

    static final class CorrectLinearCombination extends LinearCombination<BN254bFr> {
        private static final long serialVersionUID = 1L;
        @Override public BN254bFr evaluate(Assignment<BN254bFr> values) {
            BN254bFr result = BN254bFr.ZERO;
            for (LinearTerm<BN254bFr> term : terms()) {
                result = result.add(values.get(Math.toIntExact(term.index())).mul(term.value()));
            }
            return result;
        }
    }

    private static BigInteger decimal(String text, boolean coefficient) {
        if (!text.matches(coefficient ? "-?[0-9]+" : "[0-9]+")) throw new IllegalArgumentException("Nondecimal field value");
        BigInteger number = new BigInteger(text);
        if (coefficient) {
            if (number.abs().compareTo(MODULUS) >= 0) throw new IllegalArgumentException("Coefficient outside +/- scalar modulus");
            return number.mod(MODULUS);
        }
        if (number.signum() < 0 || number.compareTo(MODULUS) >= 0) throw new IllegalArgumentException("Noncanonical witness field value");
        return number;
    }

    static List<LinearTerm<BN254bFr>> combination(String text, long variables) {
        List<LinearTerm<BN254bFr>> terms = new ArrayList<>();
        if (text.isEmpty()) return terms;
        for (String pair : text.split(",", -1)) {
            String[] parts = pair.split(":", -1);
            if (parts.length != 2 || !parts[0].matches("[0-9]+")) throw new IllegalArgumentException("Malformed sparse LC: " + pair);
            long index = Long.parseLong(parts[0]);
            if (index < 0 || index >= variables) throw new IllegalArgumentException("LC variable index outside declared assignment");
            terms.add(new LinearTerm<>(index, new BN254bFr(decimal(parts[1], true))));
        }
        return terms;
    }

    private static boolean hasVariableTerm(List<LinearTerm<BN254bFr>> terms) {
        for (LinearTerm<BN254bFr> term : terms) {
            if (term.index() != 0 && !term.value().equals(BN254bFr.ZERO)) return true;
        }
        return false;
    }

    // Classify the actual TSV rows, not just the claimed N/Q metadata. The
    // scale generator uses B=1 for linear rows and nonconstant B for products.
    private static boolean scaleMultiplication(String line, long variables) {
        String[] sides = line.split("\t", -1);
        if (sides.length != 3 || !hasVariableTerm(combination(sides[0], variables))
                || !hasVariableTerm(combination(sides[2], variables))) {
            throw new IllegalArgumentException("Scale constraints must contain nonconstant A and C; vacuous 0=0 rows are forbidden");
        }
        if (sides[1].equals("0:1")) return false;
        if (!hasVariableTerm(combination(sides[1], variables))) throw new IllegalArgumentException("Scale multiplication row needs nonconstant B");
        return true;
    }

    static Tuple2<Long, BN254bFr> assignment(String line, long variables) {
        String[] parts = line.split("\t", -1);
        if (parts.length != 2 || !parts[0].matches("[0-9]+")) throw new IllegalArgumentException("Malformed assignment.tsv row");
        long index = Long.parseLong(parts[0]);
        if (index < 0 || index >= variables) throw new IllegalArgumentException("Witness index out of bounds");
        return new Tuple2<>(index, new BN254bFr(decimal(parts[1], false)));
    }

    static final class SerialInput {
        R1CSRelation<BN254bFr> relation;
        Assignment<BN254bFr> primary, auxiliary;
    }
    static SerialInput serial(Header header, long maxElements) throws Exception {
        if (header.constraints + header.primaryInputs > (1L << 30)) {
            throw new IllegalArgumentException("SERIAL_FFT_DOMAIN_LIMIT: the author's SerialFFT domainSize is int; constraints + primary_inputs must not exceed 2^30; use distributed mode");
        }
        if (header.variables > Integer.MAX_VALUE || header.constraints > Integer.MAX_VALUE || header.variables + header.constraints > maxElements) {
            throw new IllegalArgumentException("Serial input exceeds --serial-max-elements or Java array limit; use distributed mode or explicitly budget a larger serial heap/limit");
        }
        BN254bFr[] values = new BN254bFr[(int) header.variables];
        try (BufferedReader reader = Files.newBufferedReader(header.directory.resolve("assignment.tsv"), StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                Tuple2<Long, BN254bFr> value = assignment(line, header.variables);
                int index = Math.toIntExact(value._1());
                if (values[index] != null) throw new IllegalArgumentException("Duplicate witness index " + index);
                values[index] = value._2();
            }
        }
        for (BN254bFr value : values) if (value == null) throw new IllegalArgumentException("Missing witness variable");
        if (!values[0].equals(BN254bFr.ONE)) throw new IllegalArgumentException("Variable zero must be constant one");
        validatePublic(Arrays.copyOf(values, header.primaryInputs), header);
        R1CSConstraints<BN254bFr> constraints = new R1CSConstraints<>();
        long multiplicationCount = 0;
        try (BufferedReader reader = Files.newBufferedReader(header.directory.resolve("constraints.tsv"), StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (header instanceof ScaleHeader && scaleMultiplication(line, header.variables)) multiplicationCount++;
                String[] sides = line.split("\t", -1);
                if (sides.length != 3) throw new IllegalArgumentException("constraints.tsv requires exactly three sparse LCs");
                CorrectLinearCombination[] combinations = new CorrectLinearCombination[3];
                for (int side = 0; side < 3; side++) {
                    combinations[side] = new CorrectLinearCombination();
                    for (LinearTerm<BN254bFr> term : combination(sides[side], header.variables)) combinations[side].add(term);
                }
                constraints.add(new R1CSConstraint<>(combinations[0], combinations[1], combinations[2]));
            }
        }
        if (constraints.size() != header.constraints) throw new IllegalArgumentException("Constraint row count mismatch");
        if (header instanceof ScaleHeader && (multiplicationCount != header.multiplicationConstraints
                || header.constraints - multiplicationCount != header.linearConstraints)) throw new IllegalArgumentException("Scale actual multiplication/linear row counts differ from header");
        SerialInput result = new SerialInput();
        result.relation = new R1CSRelation<>(constraints, header.primaryInputs, (int) header.variables - header.primaryInputs);
        result.primary = new Assignment<>(Arrays.asList(values).subList(0, header.primaryInputs));
        result.auxiliary = new Assignment<>(Arrays.asList(values).subList(header.primaryInputs, values.length));
        return result;
    }

    static final class DistributedInput {
        R1CSRelationRDD<BN254bFr> relation;
        Assignment<BN254bFr> primary;
        JavaPairRDD<Long, BN254bFr> fullAssignment;
        JavaRDD<Tuple2<String, Long>> sourceRows;
        long sourceRowCount;
    }
    static DistributedInput distributed(Header header, JavaSparkContext sc, int partitions) {
        final long variables = header.variables;
        final long constraints = header.constraints;
        final int primaryInputs = header.primaryInputs;
        DistributedInput result = new DistributedInput();
        // No constraint or complete witness is collected on the driver.
        result.sourceRows = sc.textFile(header.directory.resolve("constraints.tsv").toUri().toString(), partitions)
                .zipWithIndex().map(row -> row).persist(STORAGE);
        result.fullAssignment = sc.textFile(header.directory.resolve("assignment.tsv").toUri().toString(), partitions)
                .mapToPair(line -> assignment(line, variables)).persist(STORAGE);
        JavaPairRDD<Long, LinearTerm<BN254bFr>>[] sides = sideArray();
        for (int side = 0; side < 3; side++) {
            final int which = side;
            sides[side] = result.sourceRows.flatMapToPair(row -> {
                if (row._2() >= constraints) throw new IllegalArgumentException("Extra constraint row");
                String[] fields = row._1().split("\t", -1);
                if (fields.length != 3) throw new IllegalArgumentException("Constraint line requires three sparse LCs");
                List<Tuple2<Long, LinearTerm<BN254bFr>>> output = new ArrayList<>();
                // Explicit zeros keep empty A/B/C rows present in upstream joins.
                output.add(new Tuple2<>(row._2(), new LinearTerm<>(0L, BN254bFr.ZERO)));
                for (LinearTerm<BN254bFr> term : combination(fields[which], variables)) output.add(new Tuple2<>(row._2(), term));
                return output.iterator();
            }).persist(STORAGE);
        }
        result.relation = new R1CSRelationRDD<>(new R1CSConstraintsRDD<>(sides[0], sides[1], sides[2], constraints), primaryInputs, variables - primaryInputs);
        return result;
    }
    @SuppressWarnings("unchecked") private static JavaPairRDD<Long, LinearTerm<BN254bFr>>[] sideArray() {
        return (JavaPairRDD<Long, LinearTerm<BN254bFr>>[]) new JavaPairRDD<?, ?>[3];
    }
    static void materialize(DistributedInput input, Header header) {
        input.sourceRowCount = input.sourceRows.count();
        if (input.sourceRowCount != header.constraints) throw new IllegalArgumentException("Constraint count differs from header");
        if (header instanceof ScaleHeader) {
            final long variables = header.variables;
            long multiplicationCount = input.sourceRows.filter(row -> scaleMultiplication(row._1(), variables)).count();
            if (multiplicationCount != header.multiplicationConstraints || input.sourceRowCount - multiplicationCount != header.linearConstraints) {
                throw new IllegalArgumentException("Scale actual multiplication/linear row counts differ from header");
            }
        }
        if (input.fullAssignment.count() != header.variables) throw new IllegalArgumentException("Witness count differs from header");
        if (input.fullAssignment.keys().distinct().count() != header.variables) throw new IllegalArgumentException("Duplicate or missing witness index");
        input.relation.constraints().A().count();
        input.relation.constraints().B().count();
        input.relation.constraints().C().count();
        final int numberPrimary = header.primaryInputs;
        List<Tuple2<Long, BN254bFr>> publicRows = input.fullAssignment.filter(row -> row._1() < numberPrimary).collect();
        BN254bFr[] publicValues = new BN254bFr[numberPrimary];
        for (Tuple2<Long, BN254bFr> row : publicRows) publicValues[Math.toIntExact(row._1())] = row._2();
        for (BN254bFr value : publicValues) if (value == null) throw new IllegalArgumentException("Missing public input");
        if (!publicValues[0].equals(BN254bFr.ONE)) throw new IllegalArgumentException("Variable zero is not constant one");
        validatePublic(publicValues, header);
        input.primary = new Assignment<>(Arrays.asList(publicValues));
        input.sourceRows.unpersist(false);
    }
    static void release(DistributedInput input) {
        input.fullAssignment.unpersist(false);
        if (input.sourceRows != null) input.sourceRows.unpersist(false);
        input.relation.constraints().A().unpersist(false);
        input.relation.constraints().B().unpersist(false);
        input.relation.constraints().C().unpersist(false);
    }

    static DistributedInput syntheticDistributed(SerialInput source, JavaSparkContext sc, int partitions) {
        DistributedInput result = new DistributedInput();
        JavaPairRDD<Long, LinearTerm<BN254bFr>>[] matrices = sideArray();
        for (int side = 0; side < 3; side++) {
            List<Tuple2<Long, LinearTerm<BN254bFr>>> rows = new ArrayList<>();
            for (int row = 0; row < source.relation.numConstraints(); row++) {
                R1CSConstraint<BN254bFr> constraint = source.relation.constraints(row);
                LinearCombination<BN254bFr> lc = side == 0 ? constraint.A() : side == 1 ? constraint.B() : constraint.C();
                rows.add(new Tuple2<>((long) row, new LinearTerm<>(0L, BN254bFr.ZERO)));
                for (LinearTerm<BN254bFr> term : lc.terms()) rows.add(new Tuple2<>((long) row, term));
            }
            matrices[side] = sc.parallelizePairs(rows, partitions).persist(STORAGE);
            matrices[side].count();
        }
        List<Tuple2<Long, BN254bFr>> witness = new ArrayList<>();
        Assignment<BN254bFr> full = new Assignment<>(source.primary, source.auxiliary);
        for (int index = 0; index < full.size(); index++) witness.add(new Tuple2<>((long) index, full.get(index)));
        result.fullAssignment = sc.parallelizePairs(witness, partitions).persist(STORAGE);
        result.fullAssignment.count();
        result.primary = source.primary;
        result.relation = new R1CSRelationRDD<>(new R1CSConstraintsRDD<>(matrices[0], matrices[1], matrices[2], source.relation.numConstraints()), source.primary.size(), source.auxiliary.size());
        return result;
    }
}
