<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="Order.aspx.cs" Inherits="Shop.Order" Title="주문" %>
<asp:Content runat="server">
  <%-- 주문 취소 버튼 --%>
  <asp:Button ID="btnCancel" runat="server" OnClick="btnCancel_Click" Text="주문 취소" />
</asp:Content>
