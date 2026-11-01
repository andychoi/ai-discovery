const pricing = require('../../core/src/pricing');

function checkout(cart) {
  return pricing.applyDiscount(cart.total, cart.discount);
}

module.exports = { checkout };
