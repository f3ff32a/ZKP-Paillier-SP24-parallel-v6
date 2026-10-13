package comparison;

import algebra.curves.barreto_naehrig.bn254b.BN254bFields.BN254bFr;
import algebra.curves.barreto_naehrig.bn254b.BN254bG1;
import algebra.curves.barreto_naehrig.bn254b.BN254bG2;
import algebra.curves.barreto_naehrig.bn254b.BN254bGT;
import algebra.curves.barreto_naehrig.bn254b.BN254bPairing;
import algebra.curves.barreto_naehrig.bn254b.bn254b_parameters.BN254bG1Parameters;
import algebra.curves.barreto_naehrig.bn254b.bn254b_parameters.BN254bG2Parameters;
import configuration.Configuration;
import org.apache.spark.SparkConf;
import org.apache.spark.api.java.JavaSparkContext;
import profiler.generation.R1CSConstruction;
import profiler.utils.SparkUtils;
import relations.objects.Assignment;
import relations.r1cs.R1CSRelation;
import scala.Tuple3;
import zk_proof_systems.zkSNARK.DistributedProver;
import zk_proof_systems.zkSNARK.SerialProver;
import zk_proof_systems.zkSNARK.Verifier;
import zk_proof_systems.zkSNARK.objects.CRS;
import zk_proof_systems.zkSNARK.objects.Proof;
import zk_proof_systems.zkSNARK.objects.ProvingKeyRDD;

import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.ObjectOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Single-VM Groth16 adapter: strict Paillier statements or explicit constraint-scale benchmarks. */
public final class DizkComparison {
    public static final String UPSTREAM_COMMIT = "29fb0f78964cd6019029d20228ee0bee3184e64d";
    private static final String[] HEADER = {
        "run_id", "scope", "same_statement", "mode", "curve", "scalar_field_modulus",
        "paillier_bits", "reference_paillier_bits", "multiplication_constraints", "linear_constraints", "generator_id",
        "messages", "constraints", "variables", "primary_inputs",
        "requested_threads", "threads", "partitions", "repeat", "input_bundle_sha256",
        "header_sha256", "constraints_sha256", "assignment_sha256", "relation_hash",
        "input_prepare_seconds", "spark_start_seconds", "rdd_materialize_seconds",
        "r1cs_validate_seconds", "setup_seconds", "prove_seconds", "verify_seconds",
        "proof_system_seconds", "total_task_seconds", "verified", "setup_query_a_count",
        "setup_query_b_count", "setup_query_h_count", "setup_delta_abc_count",
        "proof_sha256", "max_heap_bytes", "upstream_commit"
    };

    private DizkComparison() {}
    public static void main(String[] args) {
        try { run(options(args)); }
        catch (Throwable error) {
            System.err.println("DIZK_COMPARISON_FAILED " + error.getClass().getSimpleName() + ": " + error.getMessage());
            error.printStackTrace(System.err);
            System.exit(2);
        }
    }

    private static Map<String, String> options(String[] args) {
        Map<String, String> result = new LinkedHashMap<>();
        List<String> allowed = Arrays.asList("input", "mode", "threads", "partitions", "repeat", "csv", "run-id", "memory-gb", "serial-max-elements");
        for (int i = 0; i < args.length; i++) {
            if (args[i].equals("--synthetic-smoke") || args[i].equals("--adapter-self-test") || args[i].equals("--constraint-scale")) {
                if (result.put(args[i].substring(2), "true") != null) throw new IllegalArgumentException("Duplicate option");
                continue;
            }
            if (!args[i].startsWith("--") || i + 1 >= args.length) throw new IllegalArgumentException("Options require --name value");
            String name = args[i].substring(2);
            if (!allowed.contains(name)) throw new IllegalArgumentException("Unknown option " + name);
            if (result.put(name, args[++i]) != null) throw new IllegalArgumentException("Duplicate option " + name);
        }
        if (!result.containsKey("csv")) throw new IllegalArgumentException("--csv is required");
        boolean smoke = result.containsKey("synthetic-smoke");
        if (smoke == result.containsKey("input")) throw new IllegalArgumentException("Supply exactly one of --input DIR or --synthetic-smoke");
        if (smoke && result.containsKey("adapter-self-test")) throw new IllegalArgumentException("--adapter-self-test requires --input and cannot accompany --synthetic-smoke");
        if (result.containsKey("constraint-scale") && (smoke || result.containsKey("adapter-self-test"))) {
            throw new IllegalArgumentException("--constraint-scale requires --input and is mutually exclusive with --adapter-self-test and --synthetic-smoke");
        }
        return result;
    }
    private static int positive(Map<String, String> options, String name, int fallback) {
        int value = Integer.parseInt(options.getOrDefault(name, String.valueOf(fallback)));
        if (value < 1) throw new IllegalArgumentException(name + " must be positive");
        return value;
    }
    private static double seconds(long start) { return (System.nanoTime() - start) / 1e9; }

    private static Configuration config(JavaSparkContext sc, int threads, int partitions, int memory) {
        Configuration config = sc == null ? new Configuration() : new Configuration(1, threads, memory, partitions, sc, R1CSIO.STORAGE);
        config.setRuntimeFlag(false);
        config.setVerboseFlag(false);
        // Explicit input validation runs outside proof timing. Upstream debug assertions
        // also enforce unnecessary nonzero top-H assumptions on otherwise valid R1CS.
        config.setDebugFlag(false);
        byte[] entropy = new byte[32];
        new SecureRandom().nextBytes(entropy);
        config.setSecureSeed(entropy);
        return config;
    }

    private static R1CSIO.SerialInput smoke() {
        Configuration config = new Configuration();
        config.setRuntimeFlag(false);
        config.setVerboseFlag(false);
        config.setDebugFlag(false);
        config.setSeed(7L);
        Tuple3<R1CSRelation<BN254bFr>, Assignment<BN254bFr>, Assignment<BN254bFr>> construction = R1CSConstruction.serialConstruct(64, 2, BN254bFr.ONE, config);
        R1CSIO.SerialInput result = new R1CSIO.SerialInput();
        result.relation = construction._1();
        result.primary = construction._2();
        result.auxiliary = construction._3();
        return result;
    }

    private static String proofHash(Proof<BN254bG1, BN254bG2> proof) throws Exception {
        // Upstream Proof is not Serializable. The three concrete curve points
        // are converted to affine coordinates and hashed in a fixed tagged order.
        String encoding = "BN254b_gA\n" + proof.gA().toAffineCoordinates().toString()
                + "\nBN254b_gB\n" + proof.gB().toAffineCoordinates().toString()
                + "\nBN254b_gC\n" + proof.gC().toAffineCoordinates().toString() + "\n";
        return R1CSIO.hex(MessageDigest.getInstance("SHA-256").digest(encoding.getBytes(StandardCharsets.UTF_8)));
    }

    private static String smokeHash(R1CSIO.SerialInput source) throws Exception {
        MessageDigest hash = MessageDigest.getInstance("SHA-256");
        hash.update("synthetic_smoke_author_serialConstruct_64_2_seed7\n".getBytes(StandardCharsets.UTF_8));
        try (ByteArrayOutputStream bytes = new ByteArrayOutputStream(); ObjectOutputStream stream = new ObjectOutputStream(bytes)) {
            stream.writeObject(source.relation);
            stream.writeObject(source.primary);
            stream.writeObject(source.auxiliary);
            stream.flush();
            hash.update(bytes.toByteArray());
        }
        return R1CSIO.hex(hash.digest());
    }

    private static void run(Map<String, String> options) throws Exception {
        String mode = options.getOrDefault("mode", "distributed");
        if (!mode.equals("serial") && !mode.equals("distributed")) throw new IllegalArgumentException("mode must be serial or distributed");
        int threads = positive(options, "threads", 16);
        int partitions = positive(options, "partitions", threads);
        if (mode.equals("distributed") && (partitions & (partitions - 1)) != 0) {
            throw new IllegalArgumentException("partitions must be a power of two for the pinned author's radix-2 fillRDD implementation");
        }
        int repeats = positive(options, "repeat", 1);
        int memory = positive(options, "memory-gb", 16);
        long serialMaxElements = Long.parseLong(options.getOrDefault("serial-max-elements", "2000000"));
        if (serialMaxElements < 1) throw new IllegalArgumentException("serial-max-elements must be positive");
        boolean smoke = options.containsKey("synthetic-smoke");
        boolean selfTest = options.containsKey("adapter-self-test");
        boolean constraintScale = options.containsKey("constraint-scale");
        Path csv = Paths.get(options.get("csv")).toAbsolutePath();
        String runId = options.getOrDefault("run-id", "unassigned");
        if (runId.isEmpty() || runId.indexOf('\n') >= 0 || runId.indexOf('\r') >= 0) throw new IllegalArgumentException("Invalid run-id");
        if (!smoke) R1CSIO.preflight(Paths.get(options.get("input")).toAbsolutePath());
        // Initialize parameter classes first, before Spark/Kryo can initialize
        // point classes. The author's mutual static references otherwise leave
        // the parameter ONE/ZERO points with a null instance parameter object.
        final BN254bG1 g1Factory = BN254bG1Parameters.ONE;
        final BN254bG2 g2Factory = BN254bG2Parameters.ONE;
        if (g1Factory.one() == null || g1Factory.zero() == null || g2Factory.one() == null || g2Factory.zero() == null) {
            throw new IllegalStateException("Author BN254b curve parameter initialization failed");
        }
        JavaSparkContext sc = null;
        double sparkStart = 0;
        try {
            if (mode.equals("distributed")) {
                long start = System.nanoTime();
                SparkConf spark = new SparkConf().setAppName(constraintScale ? "DIZK constraint-scale benchmark" : "DIZK strict Paillier R1CS comparison")
                        .setMaster("local[" + threads + "]")
                        .set("spark.default.parallelism", String.valueOf(partitions))
                        .set("spark.sql.shuffle.partitions", String.valueOf(partitions))
                        .set("spark.serializer", "org.apache.spark.serializer.KryoSerializer")
                        .set("spark.kryo.registrationRequired", "false")
                        .set("spark.ui.enabled", "false");
                spark.registerKryoClasses(SparkUtils.zksparkClasses());
                sc = new JavaSparkContext(spark);
                sc.setLogLevel("ERROR");
                sparkStart = seconds(start);
                if (!sc.getConf().get("spark.master").equals("local[" + threads + "]")) throw new IllegalStateException("Unexpected Spark master");
            }
            for (int repeat = 1; repeat <= repeats; repeat++) {
                long taskStart = System.nanoTime();
                Map<String, String> row = new LinkedHashMap<>();
                row.put("run_id", runId);
                row.put("scope", constraintScale ? "constraint_scale_benchmark" : smoke ? "synthetic_smoke" : selfTest ? "arithmetic_self_test" : "v6_ring_lifted");
                row.put("same_statement", smoke || selfTest || constraintScale ? "false" : "true");
                row.put("mode", mode);
                row.put("curve", "BN254b");
                row.put("scalar_field_modulus", R1CSIO.MODULUS.toString());
                row.put("requested_threads", String.valueOf(threads));
                // local[N] task slots, not a claim that verifier or every job uses N workers.
                row.put("threads", String.valueOf(mode.equals("serial") ? 1 : threads));
                row.put("partitions", String.valueOf(mode.equals("serial") ? 0 : partitions));
                row.put("repeat", String.valueOf(repeat));
                row.put("spark_start_seconds", String.valueOf(repeat == 1 ? sparkStart : 0));
                row.put("max_heap_bytes", String.valueOf(Runtime.getRuntime().maxMemory()));
                row.put("upstream_commit", UPSTREAM_COMMIT);
                Configuration config = config(sc, threads, partitions, memory);
                BN254bPairing pairing = new BN254bPairing();
                R1CSIO.Header inputHeader = null;
                R1CSIO.SerialInput serialInput = null;
                R1CSIO.DistributedInput distributedInput = null;
                long start = System.nanoTime();
                if (smoke) {
                    serialInput = smoke();
                    row.put("paillier_bits", "0"); row.put("messages", "0");
                    row.put("reference_paillier_bits", "0");
                    row.put("multiplication_constraints", "0");
                    row.put("linear_constraints", "0");
                    row.put("generator_id", "not_applicable");
                    row.put("constraints", String.valueOf(serialInput.relation.numConstraints()));
                    row.put("variables", String.valueOf(serialInput.relation.numVariables()));
                    row.put("primary_inputs", String.valueOf(serialInput.primary.size()));
                    row.put("input_bundle_sha256", smokeHash(serialInput));
                    for (String key : Arrays.asList("header_sha256", "constraints_sha256", "assignment_sha256", "relation_hash")) row.put(key, "not_applicable");
                } else {
                    inputHeader = R1CSIO.header(Paths.get(options.get("input")).toAbsolutePath(), selfTest, constraintScale);
                    row.put("paillier_bits", String.valueOf(inputHeader.paillierBits));
                    row.put("reference_paillier_bits", String.valueOf(inputHeader.referencePaillierBits));
                    row.put("multiplication_constraints", String.valueOf(inputHeader.multiplicationConstraints));
                    row.put("linear_constraints", String.valueOf(inputHeader.linearConstraints));
                    row.put("generator_id", inputHeader.generatorId);
                    row.put("messages", String.valueOf(inputHeader.messages));
                    row.put("constraints", String.valueOf(inputHeader.constraints));
                    row.put("variables", String.valueOf(inputHeader.variables));
                    row.put("primary_inputs", String.valueOf(inputHeader.primaryInputs));
                    row.put("input_bundle_sha256", inputHeader.bundleHash);
                    row.put("header_sha256", inputHeader.headerHash);
                    row.put("constraints_sha256", inputHeader.constraintHash);
                    row.put("assignment_sha256", inputHeader.assignmentHash);
                    row.put("relation_hash", inputHeader.relationHash);
                    if (mode.equals("serial")) serialInput = R1CSIO.serial(inputHeader, serialMaxElements);
                }
                row.put("input_prepare_seconds", String.valueOf(seconds(start)));
                try {
                    start = System.nanoTime();
                    if (mode.equals("distributed")) {
                        if (smoke) distributedInput = R1CSIO.syntheticDistributed(serialInput, sc, partitions);
                        else {
                            distributedInput = R1CSIO.distributed(inputHeader, sc, partitions);
                            R1CSIO.materialize(distributedInput, inputHeader);
                        }
                    }
                    row.put("rdd_materialize_seconds", String.valueOf(seconds(start)));
                    start = System.nanoTime();
                    boolean satisfied = mode.equals("serial")
                            ? serialInput.relation.isValid() && serialInput.relation.isSatisfied(serialInput.primary, serialInput.auxiliary)
                            : distributedInput.relation.isValid() && distributedInput.relation.isSatisfied(distributedInput.primary, distributedInput.fullAssignment);
                    if (!satisfied) throw new IllegalArgumentException("R1CS witness does not satisfy the imported statement");
                    row.put("r1cs_validate_seconds", String.valueOf(seconds(start)));
                    start = System.nanoTime();
                    CRS<BN254bFr, BN254bG1, BN254bG2, BN254bGT> crs;
                    if (mode.equals("serial")) {
                        crs = RobustSerialSetup.generate(serialInput.relation, BN254bFr.ONE, g1Factory, g2Factory, pairing, config);
                        row.put("setup_query_a_count", String.valueOf(crs.provingKey().queryA().size()));
                        row.put("setup_query_b_count", String.valueOf(crs.provingKey().queryB().size()));
                        row.put("setup_query_h_count", String.valueOf(crs.provingKey().queryH().size()));
                        row.put("setup_delta_abc_count", String.valueOf(crs.provingKey().deltaABCG1().size()));
                    } else {
                        crs = RobustDistributedSetup.generate(distributedInput.relation, BN254bFr.ONE, g1Factory, g2Factory, pairing, config);
                        ProvingKeyRDD<BN254bFr, BN254bG1, BN254bG2> key = crs.provingKeyRDD();
                        // Mandatory Spark actions are inside setup timing, even if
                        // an upstream version changes its internal cache actions.
                        row.put("setup_query_a_count", String.valueOf(key.queryA().count()));
                        row.put("setup_query_b_count", String.valueOf(key.queryB().count()));
                        row.put("setup_query_h_count", String.valueOf(key.queryH().count()));
                        row.put("setup_delta_abc_count", String.valueOf(key.deltaABCG1().count()));
                    }
                    double setupSeconds = seconds(start);
                    row.put("setup_seconds", String.valueOf(setupSeconds));
                    start = System.nanoTime();
                    Proof<BN254bG1, BN254bG2> proof = mode.equals("serial")
                            ? SerialProver.prove(crs.provingKey(), serialInput.primary, serialInput.auxiliary, BN254bFr.ONE, config)
                            : DistributedProver.prove(crs.provingKeyRDD(), distributedInput.primary, distributedInput.fullAssignment, BN254bFr.ONE, config);
                    // Both distributed MSM reducers return concrete curve points.
                    // Hash all concrete affine proof points before ending the prove timer.
                    row.put("proof_sha256", proofHash(proof));
                    double proveSeconds = seconds(start);
                    row.put("prove_seconds", String.valueOf(proveSeconds));
                    start = System.nanoTime();
                    boolean verified = Verifier.verify(crs.verificationKey(), mode.equals("serial") ? serialInput.primary : distributedInput.primary, proof, pairing, config);
                    double verifySeconds = seconds(start);
                    if (!verified) throw new IllegalStateException("DIZK proof verification returned false");
                    row.put("verify_seconds", String.valueOf(verifySeconds));
                    row.put("proof_system_seconds", String.valueOf(setupSeconds + proveSeconds + verifySeconds));
                    row.put("total_task_seconds", String.valueOf(seconds(taskStart) + (repeat == 1 ? sparkStart : 0)));
                    row.put("verified", "true");
                    append(csv, row);
                    System.out.println("BENCH_DIZK scope=" + row.get("scope") + " same_statement=" + row.get("same_statement")
                            + " mode=" + mode + " curve=BN254b messages=" + row.get("messages")
                            + " constraints=" + row.get("constraints") + " variables=" + row.get("variables")
                            + " primary_inputs=" + row.get("primary_inputs") + " threads=" + row.get("threads")
                            + " repeat=" + repeat + " setup_seconds=" + setupSeconds + " prove_seconds=" + proveSeconds
                            + " verify_seconds=" + verifySeconds + " verified=true input_bundle_sha256=" + row.get("input_bundle_sha256"));
                } finally {
                    if (distributedInput != null) R1CSIO.release(distributedInput);
                }
            }
        } finally { if (sc != null) sc.stop(); }
    }

    private static String quote(String value) {
        if (value.indexOf(',') >= 0 || value.indexOf('"') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) return '"' + value.replace("\"", "\"\"") + '"';
        return value;
    }
    private static void append(Path file, Map<String, String> row) throws Exception {
        if (file.getParent() != null) Files.createDirectories(file.getParent());
        String header = String.join(",", HEADER);
        if (Files.exists(file) && Files.size(file) > 0) {
            try (java.io.BufferedReader reader = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
                if (!header.equals(reader.readLine())) throw new IllegalArgumentException("Existing CSV has incompatible header");
            }
        }
        boolean newHeader = !Files.exists(file) || Files.size(file) == 0;
        try (BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
            if (newHeader) { writer.write(header); writer.newLine(); }
            List<String> values = new ArrayList<>();
            for (String name : HEADER) {
                String value = row.get(name);
                if (value == null) throw new IllegalStateException("Missing CSV field " + name);
                values.add(quote(value));
            }
            writer.write(String.join(",", values)); writer.newLine();
        }
    }
}
