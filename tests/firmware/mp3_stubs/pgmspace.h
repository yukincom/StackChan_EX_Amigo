#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define PROGMEM
#define PSTR(value) (value)
#define strcpy_P strcpy
#define snprintf_P snprintf
#define memcpy_P memcpy
#define pgm_read_byte(address) (*(const uint8_t*)(address))
#define pgm_read_word(address) (*(const uint16_t*)(address))
#define pgm_read_dword(address) (*(const uint32_t*)(address))
#define pgm_read_ptr(address) (*(const void* const*)(address))
