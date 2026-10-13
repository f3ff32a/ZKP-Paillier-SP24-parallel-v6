package comparison;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Small strict JSON reader; no dependency beyond Java 8. */
final class Json {
    private final String source;
    private int position;
    private Json(String source) { this.source = source; }

    static Map<String, Object> object(String text) {
        Json parser = new Json(text);
        Object result = parser.value();
        parser.space();
        if (parser.position != text.length() || !(result instanceof Map)) {
            throw parser.error("Expected one JSON object");
        }
        @SuppressWarnings("unchecked") Map<String, Object> map = (Map<String, Object>) result;
        return map;
    }

    private IllegalArgumentException error(String message) {
        return new IllegalArgumentException(message + " at JSON offset " + position);
    }
    private void space() {
        while (position < source.length() && " \t\r\n".indexOf(source.charAt(position)) >= 0) position++;
    }
    private boolean take(char character) {
        space();
        if (position < source.length() && source.charAt(position) == character) { position++; return true; }
        return false;
    }
    private void expect(char character) {
        if (!take(character)) throw error("Expected " + character);
    }
    private Object value() {
        space();
        if (position >= source.length()) throw error("Unexpected end");
        char character = source.charAt(position);
        if (character == '"') return string();
        if (character == '{') {
            position++;
            Map<String, Object> map = new LinkedHashMap<>();
            if (take('}')) return map;
            do {
                space();
                if (position >= source.length() || source.charAt(position) != '"') throw error("Expected object key");
                String key = string();
                expect(':');
                if (map.containsKey(key)) throw error("Duplicate JSON key " + key);
                map.put(key, value());
            } while (take(','));
            expect('}');
            return map;
        }
        if (character == '[') {
            position++;
            List<Object> list = new ArrayList<>();
            if (take(']')) return list;
            do { list.add(value()); } while (take(','));
            expect(']');
            return list;
        }
        for (String token : new String[]{"true", "false", "null"}) {
            if (source.startsWith(token, position)) {
                position += token.length();
                return token.equals("null") ? null : Boolean.valueOf(token);
            }
        }
        int start = position;
        if (character == '-') position++;
        if (position >= source.length()) throw error("Invalid number");
        if (source.charAt(position) == '0') position++;
        else {
            if (source.charAt(position) < '1' || source.charAt(position) > '9') throw error("Invalid value");
            while (position < source.length() && Character.isDigit(source.charAt(position))) position++;
        }
        if (position < source.length() && source.charAt(position) == '.') {
            position++;
            int digits = position;
            while (position < source.length() && Character.isDigit(source.charAt(position))) position++;
            if (position == digits) throw error("Invalid fraction");
        }
        if (position < source.length() && "eE".indexOf(source.charAt(position)) >= 0) {
            position++;
            if (position < source.length() && "+-".indexOf(source.charAt(position)) >= 0) position++;
            int digits = position;
            while (position < source.length() && Character.isDigit(source.charAt(position))) position++;
            if (position == digits) throw error("Invalid exponent");
        }
        return new BigDecimal(source.substring(start, position));
    }
    private String string() {
        expect('"');
        StringBuilder result = new StringBuilder();
        while (position < source.length()) {
            char character = source.charAt(position++);
            if (character == '"') return result.toString();
            if (character < 32) throw error("Control character in string");
            if (character != '\\') { result.append(character); continue; }
            if (position >= source.length()) throw error("Incomplete string escape");
            character = source.charAt(position++);
            switch (character) {
                case '"': case '\\': case '/': result.append(character); break;
                case 'b': result.append('\b'); break;
                case 'f': result.append('\f'); break;
                case 'n': result.append('\n'); break;
                case 'r': result.append('\r'); break;
                case 't': result.append('\t'); break;
                case 'u':
                    if (position + 4 > source.length()) throw error("Incomplete unicode escape");
                    try { result.append((char) Integer.parseInt(source.substring(position, position + 4), 16)); }
                    catch (NumberFormatException error) { throw error("Invalid unicode escape"); }
                    position += 4;
                    break;
                default: throw error("Invalid string escape");
            }
        }
        throw error("Unterminated string");
    }
}
