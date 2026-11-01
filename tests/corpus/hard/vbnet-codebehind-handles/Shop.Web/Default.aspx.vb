Imports System

Namespace Shop.Web
    Partial Public Class DefaultPage
        Inherits System.Web.UI.Page

        Protected Sub Page_Load(ByVal sender As Object, ByVal e As EventArgs) Handles Me.Load
            If Not IsPostBack Then
                LoadOrders()
            End If
        End Sub

        Protected Sub SaveButton_Click(ByVal sender As Object, ByVal e As EventArgs) Handles SaveButton.Click
            SaveOrder()
        End Sub

        Private Sub LoadOrders()
        End Sub

        Private Sub SaveOrder()
        End Sub
    End Class
End Namespace
