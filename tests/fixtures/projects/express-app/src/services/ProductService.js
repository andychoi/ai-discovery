const Product = require('../models/Product');

class ProductService {
  async getAllProducts() {
    return Product.find();
  }

  async getProduct(id) {
    return Product.findById(id);
  }

  async createProduct(data) {
    const product = new Product(data);
    return product.save();
  }

  async searchProducts(query) {
    return Product.find({ $text: { $search: query } });
  }
}

module.exports = new ProductService();
