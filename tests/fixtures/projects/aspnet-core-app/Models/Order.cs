using System.ComponentModel.DataAnnotations;

namespace EcommerceApi.Models
{
    [Table("orders")]
    public class Order
    {
        [Key]
        public int Id { get; set; }
        public int CustomerId { get; set; }
        public DateTime CreatedAt { get; set; }
        public string Status { get; set; }
    }
}
