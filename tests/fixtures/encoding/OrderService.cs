using System;

namespace Shop.Services
{
    // 주문 서비스: 주문 생성과 취소를 처리한다.
    public class OrderService
    {
        // 주문 상태 메시지 (한글 문자열 리터럴)
        private const string CancelledMessage = "주문이 취소되었습니다";

        public string Cancel(int orderId)
        {
            // 주문 번호 검증
            if (orderId <= 0)
                throw new ArgumentException("잘못된 주문 번호");
            return Notify(CancelledMessage);
        }

        private string Notify(string message)
        {
            return message;
        }
    }
}
