package com.shop.order;

/** 주문 컨트롤러: 주문 조회와 취소 요청을 받는다. */
public class OrderController {
    // 주문 서비스 (한글 주석)
    private final OrderService orderService = new OrderService();

    public String cancel(long orderId) {
        // 주문 취소 처리
        String msg = "주문이 취소되었습니다";
        return orderService.cancel(orderId, msg);
    }

    public String find(long orderId) {
        return "주문 조회: " + orderId;
    }
}
