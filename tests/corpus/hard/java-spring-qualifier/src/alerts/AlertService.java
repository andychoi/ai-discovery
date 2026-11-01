package alerts;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;

@Service
public class AlertService {
    private final NotificationSender sender;

    public AlertService(@Qualifier("sms") NotificationSender sender) {
        this.sender = sender;
    }

    public void raise(String message) {
        sender.send(message);
    }
}
