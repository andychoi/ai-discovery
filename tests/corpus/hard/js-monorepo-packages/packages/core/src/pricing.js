function applyDiscount(total, percent) {
  return total - (total * percent) / 100;
}

module.exports = { applyDiscount };
