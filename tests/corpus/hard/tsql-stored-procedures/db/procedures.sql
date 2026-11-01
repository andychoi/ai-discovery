CREATE PROCEDURE [dbo].[usp_AuditOrder]
    @OrderId INT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO [dbo].[AuditLog] ([OrderId], [Action]) VALUES (@OrderId, 'placed')
END
GO

CREATE PROCEDURE [dbo].[usp_PlaceOrder]
    @CustomerId INT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @OrderId INT;

    IF NOT EXISTS (SELECT 1 FROM [dbo].[Customers] WHERE [Id] = @CustomerId)
        THROW 50001, 'unknown customer', 1;

    INSERT INTO [dbo].[Orders] ([CustomerId], [Status]) VALUES (@CustomerId, 'NEW');
    SET @OrderId = SCOPE_IDENTITY();

    EXEC [dbo].[usp_AuditOrder] @OrderId = @OrderId;
END
GO
