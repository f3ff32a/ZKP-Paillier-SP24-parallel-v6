import algebra.curves.barreto_naehrig.bn254b.BN254bFields.BN254bFr;
import algebra.curves.barreto_naehrig.bn254b.BN254bG1;
import algebra.curves.barreto_naehrig.bn254b.bn254b_parameters.BN254bG1Parameters;
import algebra.msm.FixedBaseMSM;
import java.math.BigInteger;
import java.security.SecureRandom;
import java.util.List;

public final class FixedMsmProbe {
  public static void main(String[] args) {
    final BN254bG1 factory=BN254bG1Parameters.ONE;
    final BigInteger modulus=BN254bFr.FrParameters.modulus();
    final BN254bFr scalar=new BN254bFr(modulus.subtract(BigInteger.ONE));
    final SecureRandom random=new SecureRandom();
    for(int i=0;i<100;i++) {
      byte[] entropy=new byte[32];random.nextBytes(entropy);
      BN254bG1 base=factory.random(null,entropy);
      int coordinateBits=base.bitSize();
      if(coordinateBits!=253) continue;
      int window=11;
      List<List<BN254bG1>> table=FixedBaseMSM.getWindowTable(base,coordinateBits,window);
      BN254bG1 fixed=FixedBaseMSM.serialMSM(coordinateBits,window,table,scalar);
      BN254bG1 direct=base.mul(scalar);
      boolean bad=fixed.equals(direct);
      List<List<BN254bG1>> safe=FixedBaseMSM.getWindowTable(base,254,window);
      boolean repaired=FixedBaseMSM.serialMSM(254,window,safe,scalar).equals(direct);
      System.out.println("base_coordinate_bits="+coordinateBits+" scalar_bits="+scalar.toBigInteger().bitLength()+" window="+window+" original_matches_direct="+bad+" scalar_width_254_matches_direct="+repaired);
      if(bad || !repaired)throw new IllegalStateException("Unexpected diagnosis result");
      return;
    }
    throw new IllegalStateException("No suitable random coordinate width found");
  }
}
