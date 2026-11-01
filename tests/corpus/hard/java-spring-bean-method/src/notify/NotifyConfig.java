package notify;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class NotifyConfig {
    @Bean
    public Sender sender() {
        return new SmsSender();
    }
}
