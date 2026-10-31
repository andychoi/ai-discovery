package com.acme;

public class Config {
    public String url() {
        String password = "Sup3rS3cretValue!";
        return "jdbc:postgresql://app:" + password + "@db/app";
    }
}
