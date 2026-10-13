package comparison;

import java.math.BigDecimal;
import java.util.Map;

/** Dependency-free parser checks; not part of the packaged Maven source tree. */
public final class JsonChecks {
    public static void main(String[] args) {
        Map<String, Object> parsed = Json.object("{\"n\":12,\"s\":\"a\\n\\u0041\",\"b\":true,\"x\":[0,null,{}]}");
        if (!parsed.get("n").equals(new BigDecimal("12")) || !parsed.get("s").equals("a\nA") || !Boolean.TRUE.equals(parsed.get("b"))) throw new AssertionError("Valid JSON parse");
        for (String invalid : new String[]{"{\"a\":1,\"a\":2}", "{\"a\":01}", "{\"a\":1,}", "{\"a\":1}x", "[]", "{\"a\":NaN}", "{\"a\":\"\\uXYZW\"}"}) {
            try { Json.object(invalid); throw new AssertionError("Accepted invalid JSON: " + invalid); }
            catch (IllegalArgumentException expected) { }
        }
        System.out.println("JsonChecks passed");
    }
}
