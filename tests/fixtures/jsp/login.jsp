<%@ page contentType="text/html" %>
<jsp:useBean id="user" class="com.app.UserBean" scope="request" />
<html><head><title>Login</title></head>
<body>
  <form action="/doLogin" method="post">
    <input name="u"/><input name="p" type="password"/>
  </form>
  <a href="bad" onclick="return false"></a>
  <form action="#"></form>
  <form action="javascript:void(0)"></form>
</body></html>
