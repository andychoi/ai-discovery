package shop.order;

import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface OrderMapper {
    List<Order> findByCustomer(@Param("customerId") long customerId, @Param("status") String status);

    int insertOrder(Order order);
}
