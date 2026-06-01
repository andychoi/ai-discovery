export const MENU = [
  { path: '/dashboard', label: 'Dashboard', roles: ['user'] },
  { path: '/admin', label: 'Admin', children: [
    { path: '/admin/users', label: 'Users' },
    { path: '/admin/roles', label: 'Roles' },
  ]},
];
