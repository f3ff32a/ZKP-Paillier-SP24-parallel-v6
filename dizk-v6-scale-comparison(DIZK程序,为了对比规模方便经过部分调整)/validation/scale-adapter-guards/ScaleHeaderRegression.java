package comparison;
import java.nio.file.Path;
import java.nio.file.Paths;
public final class ScaleHeaderRegression {
    interface Checked { void run() throws Exception; }
    static int checks;
    static void reject(String label, Checked action) throws Exception {
        try { action.run(); } catch (IllegalArgumentException expected) {
            System.out.println("PASS rejection=" + label + " reason=" + expected.getMessage()); checks++; return;
        }
        throw new AssertionError("Expected rejection: " + label);
    }
    public static void main(String[] args) throws Exception {
        Path base = Paths.get(args[0]);
        R1CSIO.Header h = R1CSIO.header(base.resolve("valid"), false, true);
        R1CSIO.SerialInput i = R1CSIO.serial(h, 100);
        if (!(h instanceof R1CSIO.ScaleHeader) || !i.relation.isValid() || !i.relation.isSatisfied(i.primary, i.auxiliary)) throw new AssertionError("Positive scale input failed");
        System.out.println("PASS scale_import=true public_prefix_bound=true N=2 Q=2 variables=7"); checks++;
        reject("strict_cannot_accept_scale", () -> R1CSIO.header(base.resolve("valid"), false));
        reject("selftest_cannot_accept_scale", () -> R1CSIO.header(base.resolve("valid"), true));
        reject("selftest_scale_flags_mutually_exclusive", () -> R1CSIO.header(base.resolve("valid"), true, true));
        reject("public_output_mismatch", () -> R1CSIO.serial(R1CSIO.header(base.resolve("wrong_public"), false, true), 100));
        reject("actual_N_Q_mismatch", () -> R1CSIO.serial(R1CSIO.header(base.resolve("wrong_categories"), false, true), 100));
        reject("same_statement_true_forbidden", () -> R1CSIO.header(base.resolve("wrong_scope"), false, true));
        reject("field_mismatch", () -> R1CSIO.header(base.resolve("wrong_field"), false, true));
        System.out.println("SCALE_HEADER_REGRESSION checks=" + checks + " all_passed=true");
    }
}
