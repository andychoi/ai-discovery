const userService = require('../services/userService');

const getUser = async (req, res) => {
  const user = await userService.findUser(req.params.id);
  res.json(user);
};

module.exports = { getUser };
