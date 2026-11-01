package alerts;

import org.springframework.stereotype.Component;

@Component("sms")
public class SmsSender implements NotificationSender {
    @Override
    public void send(String message) {
    }
}
