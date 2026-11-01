package alerts;

import org.springframework.stereotype.Component;

@Component("email")
public class EmailSender implements NotificationSender {
    @Override
    public void send(String message) {
    }
}
