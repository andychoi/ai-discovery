using EcommerceApi.Models;

namespace EcommerceApi.Services
{
    public class ProductService
    {
        private readonly IRepository<Product> _repository;

        public ProductService(IRepository<Product> repository)
        {
            _repository = repository;
        }

        public async Task<List<Product>> GetAllProducts()
        {
            return await _repository.GetAllAsync();
        }

        public async Task<Product> GetProduct(int id)
        {
            return await _repository.GetByIdAsync(id);
        }
    }
}
