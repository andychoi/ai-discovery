package com.ex.ctrl;
import com.ex.svc.OrderService;
import com.ex.svc.PaymentService;
import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Autowired;
@RestController
@RequestMapping("/checkout")
public class CheckoutController {
    @Autowired private OrderService orderService;
    @Autowired private PaymentService paymentService;

    @GetMapping
    public void view() {
        orderService.process();   // ONLY OrderService.process is correct
    }
}
