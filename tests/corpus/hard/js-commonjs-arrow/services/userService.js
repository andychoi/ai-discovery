const db = require('../db');

const findUser = async (id) => {
  return db.get(id);
};

module.exports = { findUser };
