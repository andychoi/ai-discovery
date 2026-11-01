using Microsoft.EntityFrameworkCore.Migrations;

namespace Shop.Data.Migrations
{
    public partial class Initial : Migration
    {
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql(@"
CREATE TABLE [dbo].[Customers] ([Id] INT IDENTITY(1,1) NOT NULL, [Name] NVARCHAR(200) NOT NULL,
    CONSTRAINT [PK_Customers] PRIMARY KEY CLUSTERED ([Id] ASC));
CREATE TABLE [dbo].[Orders] ([Id] INT IDENTITY(1,1) NOT NULL, [CustomerId] INT NOT NULL,
    CONSTRAINT [PK_Orders] PRIMARY KEY CLUSTERED ([Id] ASC));
ALTER TABLE [dbo].[Orders] WITH CHECK ADD CONSTRAINT [FK_Orders_Customers]
    FOREIGN KEY([CustomerId]) REFERENCES [dbo].[Customers] ([Id]);");

            migrationBuilder.Sql(@"
CREATE TABLE [dbo].[OrderLines] ([Id] INT IDENTITY(1,1) NOT NULL, [OrderId] INT NOT NULL,
    CONSTRAINT [PK_OrderLines] PRIMARY KEY CLUSTERED ([Id] ASC),
    CONSTRAINT [FK_OrderLines_Orders] FOREIGN KEY ([OrderId]) REFERENCES [dbo].[Orders] ([Id]) ON DELETE CASCADE);");
        }
    }
}
