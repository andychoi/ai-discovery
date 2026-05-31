package com.example.entity;

import javax.persistence.*;

@Entity
@Table(name = "customers")
public class Customer {
    @Id
    @GeneratedValue
    private Long id;
    private String name;
    private String email;
    private String address;

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getEmail() { return email; }
}
