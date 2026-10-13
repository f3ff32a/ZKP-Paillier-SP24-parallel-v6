package comparison;

import algebra.curves.barreto_naehrig.bn254b.BN254bFields.BN254bFr;
import algebra.curves.barreto_naehrig.bn254b.BN254bG1;
import algebra.curves.barreto_naehrig.bn254b.BN254bG2;
import algebra.curves.barreto_naehrig.bn254b.bn254b_parameters.BN254bG1Parameters;
import algebra.curves.barreto_naehrig.bn254b.bn254b_parameters.BN254bG2Parameters;
import algebra.groups.AbstractGroup;
import algebra.msm.FixedBaseMSM;

import java.math.BigInteger;
import java.util.List;
import java.util.Random;

/** Reproduce scalar truncation and compare the correction to direct multiplication. */
public final class FixedScalarWidthRegression {
    private static final BigInteger MODULUS = new BigInteger("17855808334804902850260923831770255773646114952324966112694569107431857586177");

    private static <T extends AbstractGroup<T>> void exercise(String name, T base) {
        BN254bFr scalar = new BN254bFr(MODULUS.subtract(BigInteger.ONE));
        int window = 11;
        int originalWidth = base instanceof BN254bG1 ? ((BN254bG1) base).bitSize() : ((BN254bG2) base).bitSize();
        if (originalWidth != 253 || scalar.toBigInteger().bitLength() != 254) throw new AssertionError("Probe did not reproduce the short-coordinate condition");
        T expected = base.mul(scalar);
        List<List<T>> oldTable = FixedBaseMSM.getWindowTable(base, originalWidth, window);
        T original = FixedBaseMSM.serialMSM(originalWidth, window, oldTable, scalar);
        List<List<T>> correctedTable = FixedBaseMSM.getWindowTable(base, MODULUS.bitLength(), window);
        T corrected = FixedBaseMSM.serialMSM(MODULUS.bitLength(), window, correctedTable, scalar);
        if (original.equals(expected)) throw new AssertionError("Original width unexpectedly retained the high bit");
        if (!corrected.equals(expected)) throw new AssertionError("Corrected full scalar width differs from direct multiplication");
        System.out.println(name + " coordinate_bits=" + originalWidth + " scalar_bits=254 window=11 original_matches_direct=false corrected_matches_direct=true");
    }

    public static void main(String[] args) {
        // Parameters first: avoid the independent upstream static-initialization cycle.
        BN254bG1 oneG1 = BN254bG1Parameters.ONE;
        BN254bG2 oneG2 = BN254bG2Parameters.ONE;
        Random random = new Random(20261013L);
        BN254bG1 shortG1 = null;
        BN254bG2 shortG2 = null;
        for (int attempt = 0; attempt < 256 && (shortG1 == null || shortG2 == null); attempt++) {
            BN254bFr multiplier = new BN254bFr(new BigInteger(63, random).add(BigInteger.ONE));
            if (shortG1 == null) {
                BN254bG1 candidate = oneG1.mul(multiplier).toAffineCoordinates();
                if (candidate.bitSize() == 253 && !candidate.isZero()) shortG1 = candidate;
            }
            if (shortG2 == null) {
                BN254bG2 candidate = oneG2.mul(multiplier).toAffineCoordinates();
                if (candidate.bitSize() == 253 && !candidate.isZero()) shortG2 = candidate;
            }
        }
        if (shortG1 == null || shortG2 == null) throw new AssertionError("No short-coordinate point in deterministic bounded search");
        exercise("BN254bG1", shortG1);
        exercise("BN254bG2", shortG2);
    }
}
