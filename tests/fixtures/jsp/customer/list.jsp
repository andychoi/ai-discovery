<%@ include file="../header.jsp" %>
<jsp:include page="nav.jsp" />
<jsp:useBean id="svc" class="com.acme.CustomerService" scope="session" />
<title>Customers</title>
<form action="/customers/search"></form>
