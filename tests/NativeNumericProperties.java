import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.util.Base64;
import org.apache.jmeter.save.SaveService;
import org.apache.jmeter.testelement.property.DoubleProperty;
import org.apache.jmeter.testelement.property.FloatProperty;
import org.apache.jmeter.testelement.property.IntegerProperty;
import org.apache.jmeter.testelement.property.JMeterProperty;
import org.apache.jmeter.testelement.property.LongProperty;
import org.apache.jmeter.util.JMeterUtils;

class NativeNumericProperties {
    public static void main(String[] args) throws Exception {
        JMeterUtils.setJMeterHome(args[0]);
        JMeterUtils.loadJMeterProperties(args[0] + "/bin/jmeter.properties");
        for (JMeterProperty property : new JMeterProperty[] {
                new DoubleProperty("throughput", 300.0),
                new FloatProperty("floating", 1.25f),
                new IntegerProperty("integer", 7),
                new LongProperty("long", 2147483648L)}) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            SaveService.saveElement(property, output);
            JMeterProperty loaded = (JMeterProperty) SaveService.loadElement(
                    new ByteArrayInputStream(output.toByteArray()));
            if (!property.getClass().equals(loaded.getClass())
                    || !property.getName().equals(loaded.getName())
                    || !property.getObjectValue().equals(loaded.getObjectValue())) {
                throw new AssertionError("Numeric property changed during SaveService roundtrip");
            }
            System.out.println("NATIVE\t" + Base64.getEncoder().encodeToString(output.toByteArray()));
        }
    }
}
