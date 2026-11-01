package notify;

import org.springframework.stereotype.Service;

@Service
public class Reminder {
    private final Sender sender;

    public Reminder(Sender sender) {
        this.sender = sender;
    }

    public void remind(String text) {
        sender.send(text);
    }
}
