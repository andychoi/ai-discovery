<%@ Page Language="C#" CodeBehind="Customer.aspx.cs" Inherits="MyApp.Pages.Customer, MyApp" %>
<title>Customer</title>
<asp:GridView ID="grid" runat="server" OnRowCommand="grid_RowCommand" />
