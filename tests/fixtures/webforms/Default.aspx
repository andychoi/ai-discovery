<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="Default.aspx.cs" Inherits="MyApp.Default" MasterPageFile="~/Site.Master" Title="Home" %>
<asp:Content runat="server">
  <asp:Button ID="btnSave" runat="server" OnClick="btnSave_Click" Text="Save" />
  <asp:LinkButton ID="lnkCancel" runat="server" OnClick="lnkCancel_Click">Cancel</asp:LinkButton>
</asp:Content>
