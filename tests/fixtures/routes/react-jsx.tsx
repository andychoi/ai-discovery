import { CustomerList } from './CustomerList';
import { CustomerDetail } from './CustomerDetail';
function App() {
  return (
    <Routes>
      <Route path="/customers" element={<CustomerList />}>
        <Route path=":id" element={<CustomerDetail />} />
      </Route>
      <Route path="/old" element={<Navigate to="/customers" />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
