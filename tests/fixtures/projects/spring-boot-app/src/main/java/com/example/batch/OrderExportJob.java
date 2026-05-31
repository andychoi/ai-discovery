package com.example.batch;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class OrderExportJob {
    @Scheduled(cron = "0 0 2 * * *")  // 2 AM daily
    public void exportOrders() {
        // Export orders to CSV/FTP
        System.out.println("Exporting orders...");
    }
}
