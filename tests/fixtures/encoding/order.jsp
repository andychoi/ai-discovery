<%@ page contentType="text/html; charset=EUC-KR" pageEncoding="EUC-KR" %>
<jsp:useBean id="order" class="com.shop.order.OrderBean" scope="request" />
<html><head><title>주문 조회</title></head>
<body>
  <%-- 주문 취소 폼 --%>
  <form action="/order/cancel" method="post">
    <input name="orderId"/> <button>주문 취소</button>
  </form>
</body></html>
