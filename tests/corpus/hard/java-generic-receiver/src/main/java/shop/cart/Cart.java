package shop.cart;

import java.util.ArrayList;
import java.util.List;

public class Cart {
    private final List<Item> items = new ArrayList<>();

    public int count() {
        return items.size();
    }

    public int first() {
        return items.get(0).size();
    }
}
