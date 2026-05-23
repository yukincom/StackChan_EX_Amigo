#if defined(ENABLE_WAKEWORD)

#ifndef _WAKEWORD_H
#define _WAKEWORD_H

void clear_buff();
void wakeword_setup();
void wakeword_init();
int wakeword_regist();
int wakeword_compare();
void delete_mfcc(int idx);
int wakeword_registered_count();
extern int mode;   // 0: none, <0: REGIST, >0: COMPARE

#define base_path "/spiffs"
#define filename_base "/wakeword"
#define REGISTER_MAX    (10)
#define DIST_THRESHOLD  (300)
#define WAKEWORD_REGIST_NO_RESULT (-1)
#define WAKEWORD_REGIST_NO_SLOT   (-2)

#endif //_WAKEWORD_H

#endif //ENABLE_WAKEWORD
